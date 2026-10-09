"""Tests that astrometry records its plate-solve check as a gate.

A solved image passes; an image with no solution fails and flags the run.
Both are on record, so a reader can tell "solved" from "never looked".
"""

from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.drivers import astrometry_net_driver
from astrometricslib.models.gate_result import GateStatus
from astrometricslib.models.quality_summary import AstrometryQualitySummary
from astrometricslib.models.target import Target
from astrometricslib.pipelines.astrometry import runner
from astrometricslib.pipelines.astrometry.processing import star_identifier
from astrometricslib.pipelines.pipeline_base import PipelineRequest, Result


def _validate(monkeypatch: pytest.MonkeyPatch, wcs: Any) -> AstrometryQualitySummary:
    """Run `validate_output` on a canned context.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to stub the driver statistics the summary reads.
    wcs : `Any`
        The solved coordinate system, or `None` for an unsolved image.

    Returns
    -------
    summary : `AstrometryQualitySummary`
        The summary `validate_output` built.
    """
    monkeypatch.setattr(star_identifier, "get_gaia_query_statistics", lambda: {})
    monkeypatch.setattr(astrometry_net_driver, "get_plate_solve_attempt_count", lambda: 1)
    context = SimpleNamespace(
        stellar_objects=[],
        sources_detected=5,
        solve_attempted=True,
        astrometric_residual_rms_arcsec=None,
        wcs=wcs,
        image=SimpleNamespace(header={}),
    )
    breakdown = SimpleNamespace(catalog_matched=0, position_only=0, unresolved=0)
    gaia = {"attempted": 0, "failed": 0, "circuit_breaker_tripped": False}
    result = Result(
        context=context,
        stellar_objects=[],
        payload={
            "star_id_breakdown": breakdown,
            "simbad_matched_count": 0,
            "gaia_statistics": gaia,
            "plate_solve_attempts": 1,
        },
    )
    request = PipelineRequest(target=Target(id="Test Target"), catalog_access=None, path="stack.fits")
    return runner.AstrometryPipelineAdapter().validate_output(request, result)


def test_an_unsolved_image_fails_the_gate_and_flags_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """No solution gives a failed gate, a flagged run, and the old reason."""
    summary = _validate(monkeypatch, wcs=None)

    assert summary.gate(runner.PLATE_SOLVE_GATE_NAME).status is GateStatus.FAILED
    assert summary.flagged is True
    assert "plate solve failed" in summary.flag_reasons


def test_a_solved_image_passes_the_gate_and_is_not_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A solution gives a passed gate that is on record, and no flag."""
    summary = _validate(monkeypatch, wcs=object())

    assert summary.gate(runner.PLATE_SOLVE_GATE_NAME).status is GateStatus.PASSED
    assert "plate solve failed" not in summary.flag_reasons
