"""Purpose: Capture Analysis Models.

Description: What the capture-quality analysis reports for one observing
night. It has the same three stages as the guiding analysis
(`session_quality.py`):

* Pre-processing asks whether the night's capture data is good: whether every
  exposure Ekos finished reached the frame library, whether frames carry the
  measurements later steps need, and whether exposures were cancelled often.
* Processing measures what the frames show: which exposures clipped the star,
  how sharp and round the stars were, and how much of the night was spent
  exposing.
* Post-processing turns those measurements into recommendations, each tied to
  the evidence and the equipment-derived limit it rests on.

Every result is computed on demand from the recorded frames and the
equipment's current limits, and none is stored, for the same reason as the
guiding analysis: a stored recommendation would go stale when the equipment,
and so every limit, changed.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from wayfindinglib.models.session.session_quality import Recommendation

CAPTURE_ANALYSIS_VERSION = "1.0.0"
"""Bumped whenever the capture analysis changes what it measures or reports."""


class CaptureInputQuality(BaseModel):
    """Pre-processing: how good the night's capture data is.

    Attributes
    ----------
    light_frames : `int`
        Light frames in the library for this night and this equipment.
    spectral_frames : `int`
        Of those, frames taken through a spectroscopy filter.
    imaging_frames : `int`
        The rest.
    has_ekos_record : `bool`
        Whether an Ekos session record exists for this night. Without one the
        checks that compare the library with Ekos's own list are skipped.
    ekos_light_captures : `int` or `None`
        Light exposures Ekos reports finishing. `None` without a record.
    ekos_calibration_captures : `int` or `None`
        Dark, bias and flat exposures Ekos reports finishing. They are kept
        in the calibration library, so they are not expected among the light
        frames.
    captures_without_frame : `int` or `None`
        Light exposures Ekos finished, by their saved path, that have no
        frame in the library: the frame was not downloaded, not sorted into
        a target, or deleted.
    captures_without_frame_of_unknown_kind : `int` or `None`
        Exposures with no frame whose saved path Ekos did not log. They may
        be calibration frames, or exposures never saved (focusing and
        alignment shots), so they are not counted as missing.
    frames_without_capture : `int` or `None`
        Library frames that no Ekos exposure accounts for. A high number means
        the Ekos log is incomplete, not that the frames are bad.
    match_window_seconds : `float` or `None`
        How far apart an Ekos exposure and a frame may start and still be the
        same exposure.
    aborted_captures : `int` or `None`
        Exposures cancelled before they finished.
    abort_fraction : `float` or `None`
        Cancelled exposures as a share of all started.
    abort_fraction_limit : `float` or `None`
        The share above which a night is unusual for this equipment, or
        `None` if not enough earlier nights exist to say.
    has_frequent_aborts : `bool` or `None`
        Whether `abort_fraction` exceeds `abort_fraction_limit`.
    frames_missing_measurements : `dict` [`str`, `int`]
        For each measurement later steps need, how many light frames lack it.
        Only measurements at least one frame lacks are listed.
    has_missing_measurements : `bool`
        Whether any frame lacks a needed measurement.
    sensor_temperature_spread_c : `float` or `None`
        Highest minus lowest sensor temperature among the night's frames.
    dark_temperature_tolerance_c : `float`
        How far a dark frame's temperature may differ from a light frame's
        and still calibrate it.
    frames_outside_dark_tolerance : `int`
        Frames whose temperature differs from the night's median by more than
        the tolerance, so a dark taken at the median would not suit them.
    has_enough_frames : `bool`
        Whether there are enough frames to analyse the night at all.
    limits_equipment_match : `str`
        ``"exact"`` when the night has frames taken with the equipment the
        limits were worked out for, otherwise ``"none"``.
    """

    model_config = ConfigDict(populate_by_name=True)

    light_frames: int = Field(default=0, alias="lightFrames")
    spectral_frames: int = Field(default=0, alias="spectralFrames")
    imaging_frames: int = Field(default=0, alias="imagingFrames")
    has_ekos_record: bool = Field(default=False, alias="hasEkosRecord")
    ekos_light_captures: int | None = Field(default=None, alias="ekosLightCaptures")
    ekos_calibration_captures: int | None = Field(default=None, alias="ekosCalibrationCaptures")
    captures_without_frame: int | None = Field(default=None, alias="capturesWithoutFrame")
    captures_without_frame_of_unknown_kind: int | None = Field(
        default=None, alias="capturesWithoutFrameOfUnknownKind"
    )
    frames_without_capture: int | None = Field(default=None, alias="framesWithoutCapture")
    match_window_seconds: float | None = Field(default=None, alias="matchWindowSeconds")
    aborted_captures: int | None = Field(default=None, alias="abortedCaptures")
    abort_fraction: float | None = Field(default=None, alias="abortFraction")
    abort_fraction_limit: float | None = Field(default=None, alias="abortFractionLimit")
    has_frequent_aborts: bool | None = Field(default=None, alias="hasFrequentAborts")
    frames_missing_measurements: dict[str, int] = Field(
        default_factory=dict, alias="framesMissingMeasurements"
    )
    has_missing_measurements: bool = Field(default=False, alias="hasMissingMeasurements")
    sensor_temperature_spread_c: float | None = Field(default=None, alias="sensorTemperatureSpreadC")
    dark_temperature_tolerance_c: float = Field(default=0.0, alias="darkTemperatureToleranceC")
    frames_outside_dark_tolerance: int = Field(default=0, alias="framesOutsideDarkTolerance")
    has_enough_frames: bool = Field(default=False, alias="hasEnoughFrames")
    limits_equipment_match: str = Field(default="none", alias="limitsEquipmentMatch")


class ExposureClipping(BaseModel):
    """How often one target's frames clipped at one exposure length.

    Attributes
    ----------
    target_id : `str`
        The target.
    is_spectral : `bool`
        Whether these are spectroscopy frames.
    exposure_seconds : `float`
        The exposure length.
    frames : `int`
        Frames taken at this length with a measured clipping value.
    clipped_frames : `int`
        Of those, frames with a clipped star: at least the minimum number of
        saturated pixels the science library counts as a star.
    clipped_fraction : `float`
        `clipped_frames` divided by `frames`.
    is_clipped : `bool`
        Whether a star clips at this exposure length. It is the science
        library's verdict for the target and exposure when there is one.
        Otherwise it is whether at least half the frames clipped, the rule the
        science library uses too, because one clipped frame in a group can be
        a cosmic ray or a satellite trail and says nothing about the exposure
        length.
    basis : `str`
        ``"science_stack"`` when `is_clipped` is the science library's
        verdict, ``"frame_pixel_count"`` when it was worked out here.
    science_recommended_exposure_seconds : `float` or `None`
        The exposure the science library says would keep the brightest star
        below the ceiling, when it has a verdict for this target.
    """

    model_config = ConfigDict(populate_by_name=True)

    target_id: str = Field(alias="targetId")
    is_spectral: bool = Field(alias="isSpectral")
    exposure_seconds: float = Field(alias="exposureSeconds")
    frames: int
    clipped_frames: int = Field(alias="clippedFrames")
    clipped_fraction: float = Field(alias="clippedFraction")
    is_clipped: bool = Field(alias="isClipped")
    basis: str = "frame_pixel_count"
    science_recommended_exposure_seconds: float | None = Field(
        default=None, alias="scienceRecommendedExposureSeconds"
    )


class StarQuality(BaseModel):
    """How sharp and round the night's stars were.

    Only imaging frames long enough for guiding error to show in them are
    used, and only those the science library registered, since registration
    is what measures the stars.

    Attributes
    ----------
    frames : `int`
        Frames the figures rest on.
    minimum_exposure_seconds : `float` or `None`
        Shortest exposure used, or `None` if it is not yet known.
    median_star_width_arcsec : `float` or `None`
        Median star width (FWHM), in arcseconds.
    star_width_limit_arcsec : `float` or `None`
        The width above which a night is unusual for this equipment.
    median_roundness : `float` or `None`
        Median roundness, from 0 to 1, where 1 is a circle.
    roundness_limit : `float` or `None`
        The roundness below which a night is unusual for this equipment.
    """

    model_config = ConfigDict(populate_by_name=True)

    frames: int = 0
    minimum_exposure_seconds: float | None = Field(default=None, alias="minimumExposureSeconds")
    median_star_width_arcsec: float | None = Field(default=None, alias="medianStarWidthArcsec")
    star_width_limit_arcsec: float | None = Field(default=None, alias="starWidthLimitArcsec")
    median_roundness: float | None = Field(default=None, alias="medianRoundness")
    roundness_limit: float | None = Field(default=None, alias="roundnessLimit")


class CaptureEfficiency(BaseModel):
    """How much of the night was spent exposing.

    Attributes
    ----------
    light_exposure_seconds : `float`
        Total exposure time of the night's light frames.
    span_seconds : `float`
        From the start of the first light frame to the end of the last.
    duty_cycle : `float` or `None`
        `light_exposure_seconds` divided by `span_seconds`. The rest of the
        span went to readout, slews, focusing, guiding set-up, calibration
        frames, and waiting. `None` for a single frame.
    """

    model_config = ConfigDict(populate_by_name=True)

    light_exposure_seconds: float = Field(default=0.0, alias="lightExposureSeconds")
    span_seconds: float = Field(default=0.0, alias="spanSeconds")
    duty_cycle: float | None = Field(default=None, alias="dutyCycle")


class CapturePerformance(BaseModel):
    """Processing: what the night's frames show.

    Attributes
    ----------
    clipping : `list` [`ExposureClipping`]
        Clipping for each target and exposure length, shortest exposure
        first within each target.
    star_quality : `StarQuality`
        How sharp and round the stars were.
    efficiency : `CaptureEfficiency`
        How much of the night was spent exposing.
    """

    model_config = ConfigDict(populate_by_name=True)

    clipping: list[ExposureClipping] = Field(default_factory=list)
    star_quality: StarQuality = Field(default_factory=StarQuality, alias="starQuality")
    efficiency: CaptureEfficiency = Field(default_factory=CaptureEfficiency)


class CaptureSessionAnalysis(BaseModel):
    """The full three-stage capture analysis of one observing night.

    Attributes
    ----------
    pipeline_name : `str`
        Always ``"capture"``.
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
    resolved_parameters : `dict` [`str`, `Any`]
        The limits and definitions this analysis used, so a result can be
        traced to the numbers behind it.
    input_quality : `CaptureInputQuality`
        Pre-processing: is the data good?
    performance : `CapturePerformance`
        Processing: what does it show?
    recommendations : `list` [`Recommendation`]
        Post-processing: what to do about it.
    created_at : `datetime`
        When the analysis was run.
    """

    model_config = ConfigDict(populate_by_name=True)

    pipeline_name: str = Field(default="capture", alias="pipelineName")
    pipeline_version: str = Field(default=CAPTURE_ANALYSIS_VERSION, alias="pipelineVersion")
    session_id: str = Field(alias="sessionId")
    equipment_fingerprint: str = Field(alias="equipmentFingerprint")
    flagged: bool = False
    flag_reasons: list[str] = Field(default_factory=list, alias="flagReasons")
    resolved_parameters: dict[str, Any] = Field(default_factory=dict, alias="resolvedParameters")
    input_quality: CaptureInputQuality = Field(alias="inputQuality")
    performance: CapturePerformance
    recommendations: list[Recommendation] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="createdAt")
