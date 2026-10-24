"""Purpose: The quality of one observing night, for the night review.

Description: `control.history.query(kind="night_quality", session_id=...)`
returns a `NightQuality` (`QUALITY_METRICS_PLAN.md` Section 6.1, use case UC1).
It lists each target imaged that night with where its time went (the loss
account against a stated goal and reference), the warnings the night's
measurements raise, and a count that decides whether the target needs
attention. A guiding summary sits beside it.

The same statement shapes and the same `needs_attention` function serve the
target report, so the night review and the report agree. Nothing here is
stored. It is worked out from the measurements and records the last sync
copied, and no job or log copy is started by asking.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib import MeasurementReference
from wayfindinglib.models.quality.capture_summary import CaptureSettingSummary
from wayfindinglib.models.quality.efficiency import LossAccount, ScienceGoal
from wayfindinglib.models.quality.statements import NeedsAttention, TargetStatement
from wayfindinglib.models.session.session_quality import GuidingRunPerformance

__all__ = [
    "GuidingNightSummary",
    "NightFocusDrift",
    "NightPolarAlignment",
    "NightQuality",
    "NightTargetQuality",
]


class NightTargetQuality(BaseModel):
    """What one target's frames of a night were worth.

    Parameters
    ----------
    target_id : `str`
        The target.
    goal : `ScienceGoal`
        The goal that holds most of the target's captured time. The goal is
        chosen for each capture setting (decision U2), so when the settings
        differ, each ``capture_settings`` entry states its own goal.
    frames_taken : `int`
        Frames of the night, kept and set aside.
    frames_set_aside : `int`
        Frames the stacker set aside.
    captured_seconds : `float`
        Exposure time of all frames taken, in seconds.
    efficiency_effective_seconds : `float` or `None`
        The effective time of the kept frames, in seconds. ``None`` when no
        frame could be assessed.
    capture_settings : `list` [`CaptureSettingSummary`]
        One summary for each capture setting used that night.
    loss_account : `LossAccount` or `None`
        Where the night's time went. ``None`` when no reference was
        available; `notes` says why.
    statements : `list` [`TargetStatement`]
        Warnings and notes from the night's measurements, in priority order.
    needs_attention : `NeedsAttention`
        The count and worst severity of the warnings.
    notes : `list` [`str`]
        Remarks, such as a setting with no reference or frames that could not
        be assessed.
    """

    model_config = ConfigDict(populate_by_name=True)

    target_id: str = Field(alias="targetId")
    goal: ScienceGoal
    frames_taken: int = Field(ge=0, alias="framesTaken")
    frames_set_aside: int = Field(ge=0, alias="framesSetAside")
    captured_seconds: float = Field(ge=0.0, alias="capturedSeconds")
    efficiency_effective_seconds: float | None = Field(
        default=None, ge=0.0, alias="efficiencyEffectiveSeconds"
    )
    capture_settings: list[CaptureSettingSummary] = Field(default_factory=list, alias="captureSettings")
    loss_account: LossAccount | None = Field(default=None, alias="lossAccount")
    statements: list[TargetStatement] = Field(default_factory=list)
    needs_attention: NeedsAttention = Field(alias="needsAttention")
    notes: list[str] = Field(default_factory=list)


class GuidingNightSummary(BaseModel):
    """How the night's guiding went, in a few numbers.

    Parameters
    ----------
    available : `bool`
        Whether the night has guiding data to summarize.
    reason : `str`
        Why it is not available. Empty when it is.
    flagged : `bool` or `None`
        True when the guiding analysis raised a warning.
    flag_reasons : `list` [`str`]
        The kinds of those warnings.
    rms_per_axis_arcsec : `float` or `None`
        The robust guiding error per axis, in arcseconds.
    expected_star_widening_fraction : `float` or `None`
        How much that error widens a star, as a fraction of its width.
    lost_fraction : `float` or `None`
        The share of guide frames lost.
    excursion_count : `int` or `None`
        Samples with an error far beyond the limit.
    runs : `list` [`GuidingRunPerformance`]
        The guiding runs of the night, with the error of each, so the stretches
        where guiding failed can be seen.
    """

    model_config = ConfigDict(populate_by_name=True)

    available: bool
    reason: str = ""
    flagged: bool | None = None
    flag_reasons: list[str] = Field(default_factory=list, alias="flagReasons")
    rms_per_axis_arcsec: float | None = Field(default=None, alias="rmsPerAxisArcsec")
    expected_star_widening_fraction: float | None = Field(default=None, alias="expectedStarWideningFraction")
    lost_fraction: float | None = Field(default=None, alias="lostFraction")
    excursion_count: int | None = Field(default=None, alias="excursionCount")
    runs: list[GuidingRunPerformance] = Field(default_factory=list)


class NightFocusDrift(BaseModel):
    """How star width followed the focuser temperature over a night.

    This is the plain form of the focus drift fit (`fit_focus_drift`), kept
    here so the model layer imports no analytics code.

    Parameters
    ----------
    status : `str`
        ``"drift_found"``, ``"no_drift_detected"`` or ``"insufficient_data"``.
    reason : `str`
        Why, in one sentence.
    slope_px_per_degc, slope_error_px_per_degc : `float` or `None`
        Pixels of width added per degree Celsius once the star is well out of
        focus, and its error (see `FocusDriftResult` for how the fit finds it).
    best_focus_width_px : `float` or `None`
        The star width at best focus, in pixels.
    allowed_width_increase_fraction : `float`
        How much widening the rule accepts before a refocus.
    refocus_interval_degc, interval_tolerance_degc : `float` or `None`
        Refocus after this much temperature change, and its tolerance.
    interval_resolvable : `bool` or `None`
        Whether one frame's width error is small enough to see the limit.
    frames_used, segments_used : `int`
        Frames and autofocus segments in the fit.
    temperature_change_degc : `float` or `None`
        The temperature range the fit covers.
    evidence : `list` [`MeasurementReference`]
        The rows the fit used.
    """

    model_config = ConfigDict(populate_by_name=True)

    status: str
    reason: str
    slope_px_per_degc: float | None = Field(default=None, alias="slopePxPerDegc")
    slope_error_px_per_degc: float | None = Field(default=None, alias="slopeErrorPxPerDegc")
    best_focus_width_px: float | None = Field(default=None, alias="bestFocusWidthPx")
    allowed_width_increase_fraction: float | None = Field(default=None, alias="allowedWidthIncreaseFraction")
    refocus_interval_degc: float | None = Field(default=None, alias="refocusIntervalDegc")
    interval_tolerance_degc: float | None = Field(default=None, alias="intervalToleranceDegc")
    interval_resolvable: bool | None = Field(default=None, alias="intervalResolvable")
    frames_used: int = Field(default=0, alias="framesUsed")
    segments_used: int = Field(default=0, alias="segmentsUsed")
    temperature_change_degc: float | None = Field(default=None, alias="temperatureChangeDegc")
    evidence: list[MeasurementReference] = Field(default_factory=list)


class NightPolarAlignment(BaseModel):
    """The polar-alignment error that the field rotation of a night shows.

    This is the plain form of `PolarAlignmentEstimate`. It is exploratory:
    its input metric is exploratory and no rule uses it.

    Parameters
    ----------
    exploratory : `bool`
        Always true.
    kind : `str`
        ``"fit"``, ``"lower_bound"`` or ``"insufficient_data"``.
    total_error_arcmin : `float` or `None`
        The angle between the mount's polar axis and the pole, in arcminutes
        (the least it can be, for a lower bound).
    altitude_error_arcmin, azimuth_error_arcmin : `float` or `None`
        The two parts, only for a fit.
    uncertainty_arcmin : `float` or `None`
        One standard error of the total, only for a fit.
    hour_angle_span_deg : `float` or `None`
        The widest hour-angle span of any guiding run.
    sample_count : `int`
        Frames used.
    reason : `str`
        Why the kind is what it is.
    """

    model_config = ConfigDict(populate_by_name=True)

    exploratory: bool = True
    kind: str
    total_error_arcmin: float | None = Field(default=None, alias="totalErrorArcmin")
    altitude_error_arcmin: float | None = Field(default=None, alias="altitudeErrorArcmin")
    azimuth_error_arcmin: float | None = Field(default=None, alias="azimuthErrorArcmin")
    uncertainty_arcmin: float | None = Field(default=None, alias="uncertaintyArcmin")
    hour_angle_span_deg: float | None = Field(default=None, alias="hourAngleSpanDeg")
    sample_count: int = Field(default=0, alias="sampleCount")
    reason: str


class NightQuality(BaseModel):
    """The quality of one observing night.

    Parameters
    ----------
    night_id : `str`
        The night, named for the local date on which it began.
    targets : `list` [`NightTargetQuality`]
        Each target imaged that night, those that need attention first.
    needs_attention : `NeedsAttention`
        The count and worst severity of the warnings of every target.
    guiding : `GuidingNightSummary`
        The guiding summary.
    night_statements : `list` [`TargetStatement`]
        Statements about the whole night that belong to no one target, such
        as the focus drift (one focuser serves every target), in priority
        order. They count in `needs_attention`.
    focus_drift : `NightFocusDrift` or `None`
        How star width changed with focuser temperature over the night's
        luminance frames, with the refocus interval. ``None`` when the night
        has no frames to fit; `notes` says why.
    polar_alignment : `NightPolarAlignment` or `None`
        The polar-alignment error that the field rotation shows. Always
        labeled exploratory: no rule uses it. ``None`` when there is no
        rotation data.
    notes : `list` [`str`]
        Remarks for the whole night, such as set-aside frames that are not
        recorded per frame.
    generated_at : `datetime`
        When the review was worked out.
    """

    model_config = ConfigDict(populate_by_name=True)

    night_id: str = Field(alias="nightId")
    targets: list[NightTargetQuality] = Field(default_factory=list)
    needs_attention: NeedsAttention = Field(alias="needsAttention")
    guiding: GuidingNightSummary
    night_statements: list[TargetStatement] = Field(default_factory=list, alias="nightStatements")
    focus_drift: NightFocusDrift | None = Field(default=None, alias="focusDrift")
    polar_alignment: NightPolarAlignment | None = Field(default=None, alias="polarAlignment")
    notes: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="generatedAt")
