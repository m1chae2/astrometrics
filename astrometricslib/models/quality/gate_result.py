"""Records what each quality check (a "gate") found on one pipeline run.

A pipeline's quality summary used to say only that it was flagged and give a
list of sentences. A reader could not tell a check that ran and passed from
a check that never ran: both leave nothing in the list. This module adds one
record per gate, so that "passed", "failed" and "not checked" are three
different, visible answers.

Each `GateResult` carries what was measured, the limit it was compared with,
and where that limit came from. A gate that could not run (for example, a
session with too few frames to judge) is recorded as ``not_checked`` with the
reason. It is never recorded as ``passed``.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class GateStatus(StrEnum):
    """The three possible answers of a quality check."""

    PASSED = "passed"
    FAILED = "failed"
    NOT_CHECKED = "not_checked"


class GateResult(BaseModel):
    """What one quality check found on one run.

    Parameters
    ----------
    name : `str`
        A short, stable name for the gate, such as ``"flat_noise"``. Tests and
        the UI match on it, so it must not be reworded once in use.
    status : `GateStatus`
        Whether the check passed, failed, or could not be run.
    measured_value : `float` or `None`
        The number the check measured, if it measures one.
    limit : `float` or `None`
        The limit the measured value was compared with, if there is one.
    limit_source : `str` or `None`
        Where the limit came from, such as ``"camera profile (measured)"`` or
        ``"validated on M 57 only"``.
    detail : `str`
        One plain sentence. For a failed gate it says what is wrong. For a
        ``not_checked`` gate it says why the check could not run.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str
    status: GateStatus
    measured_value: float | None = Field(default=None, alias="measuredValue")
    limit: float | None = None
    limit_source: str | None = Field(default=None, alias="limitSource")
    detail: str = ""


def passed_gate(
    name: str,
    measured_value: float | None = None,
    limit: float | None = None,
    limit_source: str | None = None,
    detail: str = "",
) -> GateResult:
    """Build the record of a gate that ran and passed.

    Parameters
    ----------
    name : `str`
        The gate's name.
    measured_value : `float` or `None`, optional
        What was measured.
    limit : `float` or `None`, optional
        The limit it was compared with.
    limit_source : `str` or `None`, optional
        Where the limit came from.
    detail : `str`, optional
        A plain sentence about the result.

    Returns
    -------
    result : `GateResult`
        A result with status ``passed``.
    """
    return GateResult(
        name=name,
        status=GateStatus.PASSED,
        measuredValue=measured_value,
        limit=limit,
        limitSource=limit_source,
        detail=detail,
    )


def failed_gate(
    name: str,
    detail: str,
    measured_value: float | None = None,
    limit: float | None = None,
    limit_source: str | None = None,
) -> GateResult:
    """Build the record of a gate that ran and failed.

    Parameters
    ----------
    name : `str`
        The gate's name.
    detail : `str`
        One sentence saying what is wrong. It is also added to the run's
        flag reasons.
    measured_value : `float` or `None`, optional
        What was measured.
    limit : `float` or `None`, optional
        The limit it was compared with.
    limit_source : `str` or `None`, optional
        Where the limit came from.

    Returns
    -------
    result : `GateResult`
        A result with status ``failed``.
    """
    return GateResult(
        name=name,
        status=GateStatus.FAILED,
        measuredValue=measured_value,
        limit=limit,
        limitSource=limit_source,
        detail=detail,
    )


def unchecked_gate(name: str, reason: str, limit_source: str | None = None) -> GateResult:
    """Build the record of a gate that could not run.

    A gate that could not run does not flag the run and does not count as a
    pass. It only states, in the record, that nothing was checked.

    Parameters
    ----------
    name : `str`
        The gate's name.
    reason : `str`
        One sentence saying why the check could not run.
    limit_source : `str` or `None`, optional
        Where the gate's limit comes from, kept so a reader can see what
        would have been checked.

    Returns
    -------
    result : `GateResult`
        A result with status ``not_checked``.
    """
    return GateResult(name=name, status=GateStatus.NOT_CHECKED, limitSource=limit_source, detail=reason)
