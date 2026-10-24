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

from wayfindinglib.models.quality.capture_summary import CaptureSettingSummary
from wayfindinglib.models.quality.efficiency import LossAccount, ScienceGoal
from wayfindinglib.models.quality.statements import NeedsAttention, TargetStatement
from wayfindinglib.models.session.session_quality import GuidingRunPerformance

__all__ = ["GuidingNightSummary", "NightQuality", "NightTargetQuality"]


class NightTargetQuality(BaseModel):
    """What one target's frames of a night were worth.

    Parameters
    ----------
    target_id : `str`
        The target.
    goal : `ScienceGoal`
        The goal its efficiency is stated for, read from the target.
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
    notes: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="generatedAt")
