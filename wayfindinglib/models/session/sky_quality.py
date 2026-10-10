"""Purpose: Sky-Position Analysis Models.

Description: What the sky-position analysis reports across every recorded
night. Where the telescope points changes how well it performs: low in the
sky the light crosses more air, and on one side of the pier the mount may
track differently from the other. This analysis asks, for each part of the
sky, whether the equipment performed measurably worse there, and where it
has too little data to say.

It follows the same three stages as the guiding and capture analyses:

* Pre-processing asks whether the data can answer the question: how many
  nights, how much of the sky they cover, and which parts of it no night
  reached.
* Processing compares each part of the sky with the equipment's typical
  performance on the same nights, and judges a part only when several nights
  support it.
* Post-processing turns the comparisons into recommendations.

Nothing here is stored. The analysis is computed from the recorded frames and
guiding runs and the equipment's current limits.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from wayfindinglib.models.session.session_quality import Recommendation

SKY_ANALYSIS_VERSION = "1.0.0"
"""Bumped whenever the sky analysis changes what it measures or reports."""


class SkySample(BaseModel):
    """One measurement of performance, with where the telescope pointed.

    Attributes
    ----------
    night : `str`
        The observing night.
    metric : `str`
        What was measured: ``"star_width"`` (arcseconds), ``"star_roundness"``
        (narrow axis over wide axis) or ``"guiding_error"`` (arcseconds per
        axis).
    value : `float`
        The measurement.
    altitude_degrees : `float` or `None`
        Altitude of the pointing, in degrees.
    azimuth_degrees : `float` or `None`
        Azimuth of the pointing, in degrees, from north through east.
    pier_side : `str` or `None`
        ``"East"`` or ``"West"``, the side of the pier the telescope was on.
    """

    model_config = ConfigDict(populate_by_name=True)

    night: str
    metric: str
    value: float
    altitude_degrees: float | None = Field(default=None, alias="altitudeDegrees")
    azimuth_degrees: float | None = Field(default=None, alias="azimuthDegrees")
    pier_side: str | None = Field(default=None, alias="pierSide")


class SkyCoverageBin(BaseModel):
    """How much data one part of the sky has.

    Attributes
    ----------
    dimension : `str`
        ``"altitude"``, ``"azimuth"`` or ``"pier_side"``.
    label : `str`
        The part, for example ``"30-45 deg"``, ``"NE"`` or ``"East"``.
    samples : `int`
        Measurements taken there.
    nights : `int`
        Distinct nights that have a measurement there.
    """

    model_config = ConfigDict(populate_by_name=True)

    dimension: str
    label: str
    samples: int
    nights: int


class SkyInputQuality(BaseModel):
    """Pre-processing: whether the data can answer the question.

    Attributes
    ----------
    nights : `int`
        Nights that have at least one measurement with a known position.
    first_night : `str` or `None`
        The earliest of them.
    last_night : `str` or `None`
        The latest of them.
    samples_by_metric : `dict` [`str`, `int`]
        Measurements with a known position, for each metric.
    samples_without_position : `int`
        Measurements left out because the frame or run has no position.
    guiding_nights_excluded : `int`
        Nights whose guiding runs were left out because the night's guiding
        data was unreliable (an impossible calibration or a weak guide star).
    altitude_range_degrees : `list` [`float`]
        Lowest and highest altitude any measurement has, or empty.
    coverage : `list` [`SkyCoverageBin`]
        The data in each part of the sky the telescope can reach.
    gaps : `list` [`str`]
        The parts with measurements on too few nights to judge, each as
        ``"dimension label"``.
    minimum_nights_per_bin : `int`
        Fewest nights a part must have to be judged.
    configured_minimum_altitude_degrees : `float` or `None`
        The lowest altitude the telescope is configured to observe at.
    has_enough_data : `bool`
        Whether enough nights exist to analyse at all.
    """

    model_config = ConfigDict(populate_by_name=True)

    nights: int = 0
    first_night: str | None = Field(default=None, alias="firstNight")
    last_night: str | None = Field(default=None, alias="lastNight")
    samples_by_metric: dict[str, int] = Field(default_factory=dict, alias="samplesByMetric")
    samples_without_position: int = Field(default=0, alias="samplesWithoutPosition")
    guiding_nights_excluded: int = Field(default=0, alias="guidingNightsExcluded")
    altitude_range_degrees: list[float] = Field(default_factory=list, alias="altitudeRangeDegrees")
    coverage: list[SkyCoverageBin] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    minimum_nights_per_bin: int = Field(default=0, alias="minimumNightsPerBin")
    configured_minimum_altitude_degrees: float | None = Field(
        default=None, alias="configuredMinimumAltitudeDegrees"
    )
    has_enough_data: bool = Field(default=False, alias="hasEnoughData")


class SkyBinResult(BaseModel):
    """How one part of the sky compares with the equipment's typical night.

    Attributes
    ----------
    dimension : `str`
        ``"altitude"``, ``"azimuth"`` or ``"pier_side"``.
    label : `str`
        The part of the sky.
    samples : `int`
        Measurements used.
    nights : `int`
        Nights that contribute a value for this part. A night contributes only
        if it also observed another part along the same dimension, because a
        night that stayed in one part cannot say how that part compares with
        another.
    median_relative_value : `float` or `None`
        The typical measurement here divided by the same night's typical
        measurement everywhere. 1.0 means no different from the rest of the
        night.
    worse_by : `float` or `None`
        How much worse than the rest of the night this part is, as a
        fraction: positive when worse, negative when better. Wider stars and
        larger guiding error are worse; less round stars are worse.
    spread : `float` or `None`
        Typical scatter of the night values along this part's dimension,
        relative to each night's typical value.
    z_score : `float` or `None`
        `worse_by` divided by its uncertainty.
    judged : `bool`
        Whether enough nights exist to judge this part.
    is_poor : `bool`
        Whether this part is measurably worse: `worse_by` is at least the
        blur tolerance and `z_score` is at least 3.
    """

    model_config = ConfigDict(populate_by_name=True)

    dimension: str
    label: str
    samples: int = 0
    nights: int = 0
    median_relative_value: float | None = Field(default=None, alias="medianRelativeValue")
    worse_by: float | None = Field(default=None, alias="worseBy")
    spread: float | None = None
    z_score: float | None = Field(default=None, alias="zScore")
    judged: bool = False
    is_poor: bool = Field(default=False, alias="isPoor")


class SkyMetricResult(BaseModel):
    """The comparison of every part of the sky for one metric.

    Attributes
    ----------
    metric : `str`
        ``"star_width"``, ``"star_roundness"`` or ``"guiding_error"``.
    nights : `int`
        Nights with enough measurements to compare within the night.
    samples : `int`
        Measurements used.
    bins : `list` [`SkyBinResult`]
        Each part of the sky.
    """

    model_config = ConfigDict(populate_by_name=True)

    metric: str
    nights: int = 0
    samples: int = 0
    bins: list[SkyBinResult] = Field(default_factory=list)


class SkyPerformance(BaseModel):
    """Processing: how each part of the sky performed.

    Attributes
    ----------
    metrics : `list` [`SkyMetricResult`]
        One per metric that has enough data.
    suggested_minimum_altitude_degrees : `float` or `None`
        The altitude below which stars were measurably wider, taken as the
        top of the highest poor altitude band. `None` if no altitude band is
        poor.
    """

    model_config = ConfigDict(populate_by_name=True)

    metrics: list[SkyMetricResult] = Field(default_factory=list)
    suggested_minimum_altitude_degrees: float | None = Field(
        default=None, alias="suggestedMinimumAltitudeDegrees"
    )


class SkyAnalysis(BaseModel):
    """The full three-stage sky-position analysis across every night.

    Attributes
    ----------
    pipeline_name : `str`
        Always ``"sky"``.
    pipeline_version : `str`
        Version of the analysis that produced this result.
    session_id : `str`
        The nights covered, as ``"first..last"``.
    equipment_fingerprint : `str`
        The equipment the analysis is for.
    flagged : `bool`
        `True` if any recommendation is a warning.
    flag_reasons : `list` [`str`]
        The kinds of the warnings.
    resolved_parameters : `dict` [`str`, `Any`]
        The tolerance and definitions the analysis used.
    input_quality : `SkyInputQuality`
        Pre-processing: can the data answer the question?
    performance : `SkyPerformance`
        Processing: how does each part of the sky compare?
    recommendations : `list` [`Recommendation`]
        Post-processing: what to do about it.
    created_at : `datetime`
        When the analysis was run.
    """

    model_config = ConfigDict(populate_by_name=True)

    pipeline_name: str = Field(default="sky", alias="pipelineName")
    pipeline_version: str = Field(default=SKY_ANALYSIS_VERSION, alias="pipelineVersion")
    session_id: str = Field(alias="sessionId")
    equipment_fingerprint: str = Field(alias="equipmentFingerprint")
    flagged: bool = False
    flag_reasons: list[str] = Field(default_factory=list, alias="flagReasons")
    resolved_parameters: dict[str, Any] = Field(default_factory=dict, alias="resolvedParameters")
    input_quality: SkyInputQuality = Field(alias="inputQuality")
    performance: SkyPerformance
    recommendations: list[Recommendation] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="createdAt")
