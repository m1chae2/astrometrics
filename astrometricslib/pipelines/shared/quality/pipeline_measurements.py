"""Shared helpers that let a pipeline emit quality measurements.

Every pipeline that records measurements next to its quality summary needs the
same few steps: make a run id, describe the subject, build a
`QualityMeasurement` from a metric declaration, turn a self-check gate into a
measurement, and store the batch. This module holds those steps once, so a
pipeline's own ``record_quality`` code only says which values to emit.

Rules the helpers keep:

- A value that is `None` is never stored as a default. It becomes a
  ``not_measured`` record with a reason.
- A measurement carries the registered ``definition_version`` of its metric,
  so a pipeline never repeats it.
- Storing is best effort. A storage failure is logged and never stops the
  science run (plan section 3.5).
- Nothing here reads the clock. The caller supplies ``measured_at``.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.foundation.errors import AstrometricsError, StorageError
from astrometricslib.models.quality.gate_result import GateResult, GateStatus
from astrometricslib.models.quality.limit import LimitTier, QualityLimit
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.models.quality.metric_spec import MetricSpec
from astrometricslib.pipelines.shared.quality.record_measurements import record_measurements

logger = logging.getLogger(__name__)

# Shown when a gate that could not measure anything also gave no sentence.
_NO_DETAIL_REASON = "The check could not measure a value and gave no further detail."


def make_run_id(job_id: str | None) -> str:
    """Give the id that groups the rows of one pipeline run.

    Parameters
    ----------
    job_id : `str` or `None`
        The id of the job the run belongs to, if there is one.

    Returns
    -------
    run_id : `str`
        The job id when there is one. Otherwise a new ``run-`` id built from
        a time-ordered UUID (version 7), so ids made later sort after ids made
        earlier and no two are the same.
    """
    if job_id:
        return job_id
    return f"run-{uuid.uuid7().hex}"


@dataclass(frozen=True)
class PipelineRunContext:
    """The facts every measurement of one pipeline run shares.

    Parameters
    ----------
    target_id : `str`
        The target the run belongs to.
    pipeline_name : `str`
        The pipeline, such as ``"asteroid_detection"``.
    pipeline_version : `str`
        The pipeline's ``*_PIPELINE_VERSION``.
    run_id : `str`
        The id that groups the run's rows (see `make_run_id`).
    job_id : `str` or `None`
        The job the run belongs to, kept as a soft link.
    measured_at : `datetime`
        When the run measured, with a time zone. The caller supplies it
        because pipeline code never reads the clock.
    """

    target_id: str
    pipeline_name: str
    pipeline_version: str
    run_id: str
    job_id: str | None
    measured_at: datetime

    @classmethod
    def from_options(
        cls,
        target_id: str,
        pipeline_name: str,
        pipeline_version: str,
        options: dict[str, Any],
    ) -> PipelineRunContext | None:
        """Build the context from a pipeline request's options.

        Parameters
        ----------
        target_id : `str`
            The target the run belongs to.
        pipeline_name : `str`
            The pipeline's name.
        pipeline_version : `str`
            The pipeline's version string.
        options : `dict`
            The request options. ``"job_id"`` (optional) and ``"measured_at"``
            are read.

        Returns
        -------
        context : `PipelineRunContext` or `None`
            The context, or `None` (with a warning in the log) when the
            options carry no ``measured_at``, because a measurement must not
            be stamped with a guessed time.
        """
        measured_at = options.get("measured_at")
        if measured_at is None:
            logger.warning(
                "No measured_at was supplied for the %s run on %s, "
                "so its quality measurements were not stored.",
                pipeline_name,
                target_id,
            )
            return None
        job_id = options.get("job_id")
        return cls(
            target_id=target_id,
            pipeline_name=pipeline_name,
            pipeline_version=pipeline_version,
            run_id=make_run_id(job_id),
            job_id=job_id,
            measured_at=measured_at,
        )

    def run_subject(self) -> QualitySubject:
        """Describe the whole run as the subject of a measurement.

        Returns
        -------
        subject : `QualitySubject`
            Level ``run``. Its id is the target id, the pipeline name and the
            run id joined with ``/``, so two runs never share a subject.
        """
        return QualitySubject(
            level=SubjectLevel.RUN,
            subjectId=f"{self.target_id}/{self.pipeline_name}/{self.run_id}",
            targetId=self.target_id,
        )

    def stack_subject(self, stack_file_name: str) -> QualitySubject:
        """Describe one stack as the subject of a measurement.

        Parameters
        ----------
        stack_file_name : `str`
            The stack file's path relative to the stacks folder. Never a
            mount path.

        Returns
        -------
        subject : `QualitySubject`
            Level ``stack``. Its id is the target id and the file name joined
            with ``/``.
        """
        return QualitySubject(
            level=SubjectLevel.STACK,
            subjectId=f"{self.target_id}/{stack_file_name}",
            targetId=self.target_id,
        )


def build_measurement(
    spec: MetricSpec,
    value: float | None,
    context: PipelineRunContext,
    subject: QualitySubject,
    *,
    reason: str = "",
    uncertainty: float | None = None,
    sample_count: int | None = None,
    status: MeasurementStatus = MeasurementStatus.MEASURED,
    check_outcome: GateStatus | None = None,
    check_limit: QualityLimit | None = None,
    action_taken: str | None = None,
) -> QualityMeasurement:
    """Build one measurement of a declared metric.

    Parameters
    ----------
    spec : `MetricSpec`
        The metric's declaration. Its name and definition version are used.
    value : `float` or `None`
        The value. `None` makes the record ``not_measured``.
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        What the value is about.
    reason : `str`, optional
        Why the value is missing. Required when `value` is `None`; for an
        ``assumed`` measurement it names the source of the assumption.
    uncertainty : `float` or `None`, optional
        The value's uncertainty, in the metric's unit.
    sample_count : `int` or `None`, optional
        How many samples are behind the value.
    status : `MeasurementStatus`, optional
        ``measured`` (the default) or ``assumed``. Ignored when `value` is
        `None`.
    check_outcome : `GateStatus` or `None`, optional
        For a self-check, whether it passed, failed or was not checked.
    check_limit : `QualityLimit` or `None`, optional
        For a self-check, the limit it used.
    action_taken : `str` or `None`, optional
        For a processing decision, what was done.

    Returns
    -------
    measurement : `QualityMeasurement`
        A measurement with origin ``measured`` and the context's run id, job
        id, pipeline version and time.
    """
    return QualityMeasurement(
        metric=spec.name,
        definitionVersion=spec.definition_version,
        subject=subject,
        status=MeasurementStatus.NOT_MEASURED if value is None else status,
        value=value,
        uncertainty=None if value is None else uncertainty,
        sampleCount=sample_count,
        reason=reason,
        checkOutcome=check_outcome,
        checkLimit=check_limit,
        actionTaken=action_taken,
        origin=MeasurementOrigin.MEASURED,
        pipelineVersion=context.pipeline_version,
        runId=context.run_id,
        jobId=context.job_id,
        measuredAt=context.measured_at,
    )


def gate_measurement(
    gate: GateResult,
    spec: MetricSpec,
    context: PipelineRunContext,
    subject: QualitySubject,
    *,
    limit_tier: LimitTier,
    register_entry: str | None = None,
) -> QualityMeasurement:
    """Turn a self-check gate into a measurement.

    The measurement keeps the gate's outcome next to its number, so a reader
    can tell a check that passed from one that could not run.

    Parameters
    ----------
    gate : `GateResult`
        The gate as the pipeline already recorded it.
    spec : `MetricSpec`
        The declaration of the metric that stands for this gate.
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        What the check is about, usually the run.
    limit_tier : `LimitTier`
        How the gate's limit was obtained. The caller chooses it, because the
        gate does not say. A limit with no validation register entry must not
        use ``validated_dataset``.
    register_entry : `str` or `None`, optional
        The register entry that backs the limit. Required for
        ``validated_dataset``.

    Returns
    -------
    measurement : `QualityMeasurement`
        ``value`` is the gate's measured value; ``check_outcome`` is its
        status; ``check_limit`` is built from its limit and limit source
        (in the metric's unit); ``reason`` is its detail. A gate with no
        measured value gives a ``not_measured`` record that still carries the
        outcome. A gate that passed or failed but gave no numeric limit
        carries a limit whose value is `None` and whose derivation is the
        limit source.
    """
    has_limit_information = gate.limit is not None or bool(gate.limit_source)
    needs_limit = gate.status in (GateStatus.PASSED, GateStatus.FAILED)
    check_limit = None
    if has_limit_information or needs_limit:
        check_limit = QualityLimit(
            value=gate.limit,
            unit=spec.unit,
            tier=limit_tier,
            derivation=gate.limit_source or "No limit source was recorded for this check.",
            registerEntry=register_entry,
        )
    reason = gate.detail
    if gate.measured_value is None and not reason.strip():
        reason = _NO_DETAIL_REASON
    return build_measurement(
        spec,
        gate.measured_value,
        context,
        subject,
        reason=reason,
        check_outcome=gate.status,
        check_limit=check_limit,
    )


def record_run_measurements(measurements: Sequence[QualityMeasurement], *, config: Any = None) -> int:
    """Store a pipeline run's measurements, never failing the run.

    Parameters
    ----------
    measurements : `Sequence` of `QualityMeasurement`
        The run's measurements. All are checked against the metric registry
        and written in one call.
    config : `AppConfiguration`, optional
        Where the quality store lives. The process-wide configuration is used
        if omitted.

    Returns
    -------
    new_count : `int`
        How many rows were new. A repeat of a stored run, metric and subject
        writes none. It is 0 when storing failed.

    Notes
    -----
    A `StorageError` is logged as a warning and swallowed (plan section 3.5).
    A measurement that breaks a registry rule is a programming error; its
    `AstrometricsError` is logged as an error and also swallowed, because a
    quality row must never stop a science result.
    """
    if not measurements:
        return 0
    try:
        return record_measurements(QualityStore.from_configuration(config), measurements)
    except StorageError as error:
        logger.warning("Quality measurements could not be stored: %s", error)
    except AstrometricsError:
        logger.exception("Quality measurements were refused by the metric registry")
    return 0
