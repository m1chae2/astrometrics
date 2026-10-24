"""Purpose: The shared shape of a quality statement.

Description: A statement is one sentence the library says about a target's
data: a warning about frames already taken, a recommendation for the next
session, a limit of the equipment, or a note that data is missing. This
module defines the words used to classify a statement (`ActionClass`,
`StatementKind`, `Confidence`, `Severity`), the typed values a statement may
propose (`ProposedAction`) or promise (`ExpectedEffect`), and the statement
itself (`TargetStatement`).

Rules the models enforce:

* A warning has a severity. A warning or a recommendation names one action
  class. A limit of the equipment (``constraint``) and a note that data is
  missing (``insufficient_data``) name none and propose nothing, because the
  library never advises buying hardware and never hides missing data.
* A statement cites the exact measurements and limits it used.
* The confidence of a statement comes from the status label of the
  validation register entry behind its rule (register entry WAY-01).

Statements are recomputed on each read and never stored.
"""

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from astrometricslib import CaptureSetting, MeasurementReference, QualityLimit


class ActionClass(StrEnum):
    """The kind of change a warning or recommendation asks for.

    There is no hardware class: the library never advises a purchase.
    """

    CAPTURE_SETTINGS = "capture_settings"
    SCHEDULING = "scheduling"
    CALIBRATION = "calibration"
    DATA_SELECTION = "data_selection"
    SESSION_PROCEDURE = "session_procedure"
    ADJUSTMENT = "adjustment"


class StatementKind(StrEnum):
    """What a statement is.

    `WARNING` is about data already taken. `RECOMMENDATION` is about the
    next session. `CONSTRAINT` states a limit of the equipment and gives no
    advice. `INSUFFICIENT_DATA` says what is missing and how to get it.
    """

    WARNING = "warning"
    RECOMMENDATION = "recommendation"
    CONSTRAINT = "constraint"
    INSUFFICIENT_DATA = "insufficient_data"


class Confidence(StrEnum):
    """How well a rule is backed, in the validation register's status labels.

    A design estimate is shown to the user as tentative.
    """

    MEASURED = "measured"
    PARTLY_MEASURED = "partly_measured"
    DESIGN_ESTIMATE = "design_estimate"


class Severity(StrEnum):
    """How much attention a warning needs.

    `INFO` states a finding with nothing to do. `ADVICE` suggests a change
    that would improve results. `WARNING` names a problem that makes the
    data unreliable or the equipment misconfigured. The per-night analyses
    use the same scale for their statements.
    """

    INFO = "info"
    ADVICE = "advice"
    WARNING = "warning"


class ProposedAction(BaseModel):
    """The typed values a recommendation proposes.

    At least one field must be set. Each value must be one the equipment
    allows; the code that builds the statement checks that, not this model.

    Parameters
    ----------
    exposure_seconds : `float` or `None`
        Exposure time of one frame, in seconds.
    frame_count : `int` or `None`
        How many frames to take.
    gain_setting : `int` or `None`
        The camera's gain setting.
    offset : `int` or `None`
        The camera's offset.
    binning : `int` or `None`
        The binning factor.
    filter_name : `str` or `None`
        The filter to use.
    dither_every_frames : `int` or `None`
        Dither after this many frames.
    refocus_every_degc : `float` or `None`
        Refocus after the focuser temperature changes by this many degrees
        Celsius.
    window_start : `datetime` or `None`
        Start of a good observing window.
    window_end : `datetime` or `None`
        End of a good observing window.
    calibration_frame_type : `str` or `None`
        ``"dark"``, ``"flat"`` or ``"bias"`` for a calibration action.
    note : `str` or `None`
        A short plain-language detail that fits no other field.
    """

    model_config = ConfigDict(populate_by_name=True)

    exposure_seconds: float | None = Field(default=None, gt=0.0, alias="exposureSeconds")
    frame_count: int | None = Field(default=None, ge=1, alias="frameCount")
    gain_setting: int | None = Field(default=None, alias="gainSetting")
    offset: int | None = None
    binning: int | None = Field(default=None, ge=1)
    filter_name: str | None = Field(default=None, alias="filterName")
    dither_every_frames: int | None = Field(default=None, ge=1, alias="ditherEveryFrames")
    refocus_every_degc: float | None = Field(default=None, gt=0.0, alias="refocusEveryDegc")
    window_start: datetime | None = Field(default=None, alias="windowStart")
    window_end: datetime | None = Field(default=None, alias="windowEnd")
    calibration_frame_type: str | None = Field(default=None, alias="calibrationFrameType")
    note: str | None = None

    @model_validator(mode="after")
    def _require_something_and_ordered_window(self) -> Self:
        """Check that the proposal says something and its window is in order.

        Returns
        -------
        action : `ProposedAction`
            This action, unchanged.

        Raises
        ------
        ValueError
            If no field is set, or the window ends before it starts.
        """
        if all(getattr(self, name) is None for name in type(self).model_fields):
            raise ValueError("A proposed action must set at least one field.")
        if (
            self.window_start is not None
            and self.window_end is not None
            and self.window_end <= self.window_start
        ):
            raise ValueError("window_end must be later than window_start.")
        return self


class ExpectedEffect(BaseModel):
    """What a proposed action is expected to do.

    Parameters
    ----------
    noise_change_fraction : `float` or `None`
        Expected change in stack noise as a fraction. A fall of 29% is -0.29.
    efficiency_change : `float` or `None`
        Expected change in efficiency (a number from 0 to 1).
    effective_time_added_seconds : `float` or `None`
        Effective exposure time the action adds, in seconds.
    wall_clock_seconds : `float` or `None`
        Time on the clock the action takes, with downloads, dithers and
        autofocus, in seconds.
    description : `str`
        The effect in one plain sentence.
    """

    model_config = ConfigDict(populate_by_name=True)

    noise_change_fraction: float | None = Field(default=None, alias="noiseChangeFraction")
    efficiency_change: float | None = Field(default=None, alias="efficiencyChange")
    effective_time_added_seconds: float | None = Field(default=None, alias="effectiveTimeAddedSeconds")
    wall_clock_seconds: float | None = Field(default=None, ge=0.0, alias="wallClockSeconds")
    description: str


class ChecklistReturn(BaseModel):
    """Says that a ticked checklist item has come back.

    Parameters
    ----------
    item_id : `str`
        The checklist item that was ticked.
    ticked_at : `datetime`
        When the user ticked it.
    reason : `str`
        Why it came back, such as "stars still 28% wider on Oct 15".
    """

    model_config = ConfigDict(populate_by_name=True)

    item_id: str = Field(alias="itemId")
    ticked_at: datetime = Field(alias="tickedAt")
    reason: str


class TargetStatement(BaseModel):
    """One statement about the data of a target.

    Parameters
    ----------
    rule : `str`
        The name of the rule that produced it.
    kind : `StatementKind`
        Warning, recommendation, constraint or insufficient data.
    severity : `Severity` or `None`
        How much attention it needs. Required for a warning.
    action_class : `ActionClass` or `None`
        The kind of change it asks for. Required for a warning or a
        recommendation, and ``None`` otherwise.
    capture_setting : `CaptureSetting` or `None`
        The capture setting it is about, when it is about one.
    message : `str`
        One plain sentence.
    reason : `str`
        Why. For ``insufficient_data``, what is missing and how to get it.
    proposed_action : `ProposedAction` or `None`
        The values proposed. ``None`` for a constraint or missing data.
    expected_effect : `ExpectedEffect` or `None`
        What the action is expected to do.
    evidence : `list` [`MeasurementReference`]
        The exact measurements used.
    evidence_omitted_count : `int`
        How many references were left out of ``evidence`` to keep a long
        reply short. Zero when none were left out.
    limits : `list` [`QualityLimit`]
        The limits they were compared with.
    values : `dict` [`str`, `float` or `str` or `None`]
        Numbers the statement rests on that are not rows of the measurement
        store, such as guide-log values and counts. They stay readable by
        machines so a reader need not parse the message. The keys are plain
        names. Empty when every number is cited as evidence.
    confidence : `Confidence`
        How well the rule is backed.
    checklist_return : `ChecklistReturn` or `None`
        Set when a ticked checklist item came back.
    """

    model_config = ConfigDict(populate_by_name=True)

    rule: str
    kind: StatementKind
    severity: Severity | None = None
    action_class: ActionClass | None = Field(default=None, alias="actionClass")
    capture_setting: CaptureSetting | None = Field(default=None, alias="captureSetting")
    message: str
    reason: str = ""
    proposed_action: ProposedAction | None = Field(default=None, alias="proposedAction")
    expected_effect: ExpectedEffect | None = Field(default=None, alias="expectedEffect")
    evidence: list[MeasurementReference] = Field(default_factory=list)
    evidence_omitted_count: int = Field(default=0, ge=0, alias="evidenceOmittedCount")
    limits: list[QualityLimit] = Field(default_factory=list)
    values: dict[str, float | str | None] = Field(default_factory=dict)
    confidence: Confidence
    checklist_return: ChecklistReturn | None = Field(default=None, alias="checklistReturn")

    @model_validator(mode="after")
    def _check_fields_fit_the_kind(self) -> Self:
        """Check that the severity, action class and reason fit the kind.

        Returns
        -------
        statement : `TargetStatement`
            This statement, unchanged.

        Raises
        ------
        ValueError
            If a warning has no severity, a warning or recommendation has no
            action class, a constraint or insufficient-data statement has an
            action class or a proposed action, or insufficient data has no
            reason.
        """
        if self.kind == StatementKind.WARNING and self.severity is None:
            raise ValueError("A warning must have a severity.")
        if self.kind in (StatementKind.WARNING, StatementKind.RECOMMENDATION):
            if self.action_class is None:
                raise ValueError(f"A {self.kind.value} must name one action class.")
        else:
            if self.action_class is not None or self.proposed_action is not None:
                raise ValueError(f"A {self.kind.value} statement has no action class and proposes no action.")
        if self.kind == StatementKind.INSUFFICIENT_DATA and not self.reason.strip():
            raise ValueError("An insufficient_data statement must say what is missing in its reason.")
        return self


class NeedsAttention(BaseModel):
    """The count and worst severity of the warnings of a target or night.

    Parameters
    ----------
    warning_count : `int`
        How many warnings there are.
    worst_severity : `Severity` or `None`
        The most severe of them. ``None`` exactly when there are none.
    """

    model_config = ConfigDict(populate_by_name=True)

    warning_count: int = Field(ge=0, alias="warningCount")
    worst_severity: Severity | None = Field(default=None, alias="worstSeverity")

    @model_validator(mode="after")
    def _check_count_matches_severity(self) -> Self:
        """Check that a severity is given exactly when there are warnings.

        Returns
        -------
        attention : `NeedsAttention`
            This object, unchanged.

        Raises
        ------
        ValueError
            If the count and the worst severity disagree.
        """
        if (self.warning_count == 0) != (self.worst_severity is None):
            raise ValueError("worst_severity must be None exactly when warning_count is 0.")
        return self
