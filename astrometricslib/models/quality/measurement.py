"""The shared shape of one stored quality measurement.

A measurement is a fact: what was measured, about which frame, stack or run,
with what uncertainty, by which pipeline run. It holds no judgment about
whether the value is good; that depends on a limit and is computed when read.

This module defines the subject a measurement is about (`QualitySubject`),
the measurement itself (`QualityMeasurement`), and the references that let a
reader cite a measurement as evidence (`MeasurementReference`).

A value that was not measured is never stored as a default number. Such a
record has the status ``not_measured``, no value and a reason.
"""

import math
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from astrometricslib.models.quality.gate_result import GateStatus
from astrometricslib.models.quality.limit import QualityLimit


class SubjectLevel(StrEnum):
    """What a measurement describes. There is no night or target level."""

    FRAME = "frame"
    STACK = "stack"
    RUN = "run"


class QualitySubject(BaseModel):
    """The thing a measurement is about.

    Parameters
    ----------
    level : `SubjectLevel`
        Whether the subject is a frame, a stack or a run.
    subject_id : `str`
        A stable id: target plus relative frame path, stack id or run id.
        Never a mount path.
    target_id : `str` or `None`
        The target the subject belongs to.
    observing_night_id : `str` or `None`
        A plain time label for the observing night.
    capture_setting_id : `int` or `None`
        Row in the capture-setting table.
    channel : `str` or `None`
        The color channel for a one-shot-color camera, else ``None``.
    variant : `str` or `None`
        ``None`` for a library stack (and for every frame and run). For a
        subset stack made from some of a library stack's frames, the subset's
        label. A reader filters on this field; it never has to parse the
        subject id for ``_subsets/``.
    """

    model_config = ConfigDict(populate_by_name=True)

    level: SubjectLevel
    subject_id: str = Field(alias="subjectId")
    target_id: str | None = Field(default=None, alias="targetId")
    observing_night_id: str | None = Field(default=None, alias="observingNightId")
    capture_setting_id: int | None = Field(default=None, alias="captureSettingId")
    channel: str | None = None
    variant: str | None = Field(default=None, alias="variant")


class MeasurementStatus(StrEnum):
    """Whether a value was measured, assumed, or not measured at all."""

    MEASURED = "measured"
    ASSUMED = "assumed"
    NOT_MEASURED = "not_measured"


class MeasurementOrigin(StrEnum):
    """How a stored row came to exist."""

    MEASURED = "measured"
    BACKFILLED = "backfilled"


class QualityMeasurement(BaseModel):
    """One measured fact about a subject.

    Parameters
    ----------
    metric : `str`
        A registered metric name, such as ``"frame.star_width_moffat_arcsec"``.
    definition_version : `int`
        The registered definition version of that metric.
    subject : `QualitySubject`
        What the measurement is about.
    status : `MeasurementStatus`
        Measured, assumed or not measured.
    value : `float` or `None`
        The value. ``None`` exactly when the status is ``not_measured``.
    uncertainty : `float` or `None`
        The uncertainty of the value, in the same unit.
    sample_count : `int` or `None`
        The number of samples behind the value, such as the number of stars.
    reason : `str`
        Why the value is missing or assumed. Required for ``not_measured``.
    check_outcome : `GateStatus` or `None`
        For a self-check, whether it passed, failed or was not checked.
    check_limit : `QualityLimit` or `None`
        For a self-check, the limit it used.
    action_taken : `str` or `None`
        For a processing decision, what was done, such as
        ``"frame moved to _excluded/"``.
    origin : `MeasurementOrigin`
        Whether a pipeline run wrote the row or it was backfilled.
    pipeline_version : `str` or `None`
        The pipeline version. ``None`` when a backfilled row did not record it.
    run_id : `str`
        The job id, or a generated id when there was no job.
    job_id : `str` or `None`
        A soft link to the activity record of the job.
    measured_at : `datetime`
        When the measurement was made. It must carry a time zone.
    row_id : `int` or `None`
        The store's row id. Filled only when the measurement is read back.
    unit : `str` or `None`
        The metric's unit from its declaration. Not stored:
        `QualityRecords.query` fills it on a read.
    """

    model_config = ConfigDict(populate_by_name=True)

    metric: str
    definition_version: int = Field(alias="definitionVersion")
    subject: QualitySubject
    status: MeasurementStatus
    value: float | None = None
    uncertainty: float | None = None
    sample_count: int | None = Field(default=None, alias="sampleCount")
    reason: str = ""
    check_outcome: GateStatus | None = Field(default=None, alias="checkOutcome")
    check_limit: QualityLimit | None = Field(default=None, alias="checkLimit")
    action_taken: str | None = Field(default=None, alias="actionTaken")
    origin: MeasurementOrigin
    pipeline_version: str | None = Field(default=None, alias="pipelineVersion")
    run_id: str = Field(alias="runId")
    job_id: str | None = Field(default=None, alias="jobId")
    measured_at: datetime = Field(alias="measuredAt")
    row_id: int | None = Field(default=None, alias="rowId")
    unit: str | None = None

    @field_validator("value", "uncertainty")
    @classmethod
    def _require_finite_number(cls, number: float | None) -> float | None:
        """Refuse NaN and infinity, which no measurement may hold.

        Parameters
        ----------
        number : `float` or `None`
            The value or uncertainty being set.

        Returns
        -------
        number : `float` or `None`
            The same number.

        Raises
        ------
        ValueError
            If the number is NaN or infinite.
        """
        if number is not None and not math.isfinite(number):
            raise ValueError("A value or uncertainty must be a finite number, not NaN or infinity.")
        return number

    @field_validator("measured_at")
    @classmethod
    def _require_timezone(cls, moment: datetime) -> datetime:
        """Refuse a time with no time zone.

        Parameters
        ----------
        moment : `datetime`
            The time being set.

        Returns
        -------
        moment : `datetime`
            The same time.

        Raises
        ------
        ValueError
            If the time has no time zone.
        """
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("measured_at must carry a time zone (for example UTC); a naive time is refused.")
        return moment

    @model_validator(mode="after")
    def _check_value_matches_status(self) -> Self:
        """Check that the value, reason and self-check fit the status.

        ``not_measured`` needs a reason and no value; the others need a
        value. ``assumed`` also needs a reason that names the source of the
        assumption. A self-check that passed or failed needs the limit it
        used.

        Returns
        -------
        measurement : `QualityMeasurement`
            This measurement, unchanged.

        Raises
        ------
        ValueError
            If the value, reason or limit do not fit the status.
        """
        if self.status == MeasurementStatus.NOT_MEASURED:
            if self.value is not None:
                raise ValueError("A not_measured measurement must have value None.")
            if not self.reason.strip():
                raise ValueError("A not_measured measurement must give a reason.")
        elif self.value is None:
            raise ValueError(f"A {self.status.value} measurement must have a value.")
        if self.status == MeasurementStatus.ASSUMED and not self.reason.strip():
            raise ValueError("An assumed measurement must give a reason that names the source of the value.")
        if self.check_outcome in (GateStatus.PASSED, GateStatus.FAILED) and self.check_limit is None:
            raise ValueError(f"A {self.check_outcome.value} check must give the check_limit it used.")
        return self


class StarResultReference(BaseModel):
    """Points at one per-star result, which has no row id of its own.

    Parameters
    ----------
    star_id : `str`
        The star's identifier.
    run_id : `str`
        The run that produced the result.
    metric : `str`
        A registered metric name.
    """

    model_config = ConfigDict(populate_by_name=True)

    star_id: str = Field(alias="starId")
    run_id: str = Field(alias="runId")
    metric: str


class MeasurementReference(BaseModel):
    """Points at the exact measurement a judgment or report used.

    Exactly one of `row_id` and `star_result` is set.

    Parameters
    ----------
    row_id : `int` or `None`
        A row in the measurement store.
    star_result : `StarResultReference` or `None`
        A per-star result.
    """

    model_config = ConfigDict(populate_by_name=True)

    row_id: int | None = Field(default=None, alias="rowId")
    star_result: StarResultReference | None = Field(default=None, alias="starResult")

    @model_validator(mode="after")
    def _require_exactly_one_target(self) -> Self:
        """Require that exactly one of `row_id` and `star_result` is set.

        Returns
        -------
        reference : `MeasurementReference`
            This reference, unchanged.

        Raises
        ------
        ValueError
            If neither or both are set.
        """
        if (self.row_id is None) == (self.star_result is None):
            raise ValueError("Set exactly one of row_id and star_result.")
        return self
