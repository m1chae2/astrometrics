"""Tests for the gate record: passed, failed and not-checked stay distinct.

The point of the record is that a check which never ran must not look like
a check that passed. These tests pin that rule down, together with the way
a failed gate flags its run.
"""

import pytest

from astrometricslib.models.gate_result import (
    GateStatus,
    failed_gate,
    passed_gate,
    unchecked_gate,
)
from astrometricslib.models.quality_summary import AstrometryQualitySummary


def _empty_summary() -> AstrometryQualitySummary:
    """Build a minimal astrometry summary with no flags and no gates.

    Returns
    -------
    summary : `AstrometryQualitySummary`
        A summary that has not been flagged and has no gates recorded.
    """
    return AstrometryQualitySummary.model_validate({
        "targetId": "TEST",
        "astrometryMetrics": {
            "sourcesDetected": 0,
            "solveAttempted": False,
            "plateSolveSucceeded": False,
            "simbadMatchedCount": 0,
        },
    })


def test_failed_gate_flags_the_run_and_adds_its_reason() -> None:
    """A failed gate sets `flagged` and appends its sentence once."""
    summary = _empty_summary()
    summary.record_gate(failed_gate("plate_solve", "plate solve failed"))
    summary.record_gate(failed_gate("plate_solve", "plate solve failed"))

    assert summary.flagged is True
    assert summary.flag_reasons == ["plate solve failed"]
    assert len(summary.gates) == 1


def test_passed_gate_does_not_flag_the_run() -> None:
    """A gate that ran and passed leaves the run unflagged but is on record."""
    summary = _empty_summary()
    summary.record_gate(passed_gate("plate_solve", measured_value=0.8, limit=2.0))

    assert summary.flagged is False
    assert summary.flag_reasons == []
    assert summary.gate("plate_solve").status is GateStatus.PASSED


def test_unchecked_gate_is_recorded_and_is_not_a_pass() -> None:
    """A gate that could not run is recorded as `not_checked`, not `passed`."""
    summary = _empty_summary()
    summary.record_gate(unchecked_gate("quarantine", "3 frames are too few to judge"))

    recorded = summary.gate("quarantine")
    assert recorded is not None
    assert recorded.status is GateStatus.NOT_CHECKED
    assert recorded.status is not GateStatus.PASSED
    assert summary.flagged is False
    assert recorded.detail == "3 frames are too few to judge"


def test_rerecording_a_gate_replaces_the_earlier_answer() -> None:
    """Running the same check twice leaves one answer, the later one."""
    summary = _empty_summary()
    summary.record_gate(unchecked_gate("quarantine", "too few frames"))
    summary.record_gate(passed_gate("quarantine"))

    assert [gate.status for gate in summary.gates] == [GateStatus.PASSED]


def test_unknown_gate_name_returns_none() -> None:
    """Looking up a gate never recorded gives `None`, not a default pass."""
    assert _empty_summary().gate("never_recorded") is None


def test_gates_survive_a_save_and_load_round_trip() -> None:
    """The gate list is kept when the summary is written out and read back."""
    summary = _empty_summary()
    summary.record_gate(failed_gate("flat_noise", "flat noise too high", 0.012, 0.005, "derived"))

    restored = AstrometryQualitySummary.model_validate(summary.model_dump(by_alias=True))

    gate = restored.gate("flat_noise")
    assert gate is not None
    assert gate.status is GateStatus.FAILED
    assert gate.measured_value == pytest.approx(0.012)
    assert gate.limit_source == "derived"
