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


def _validate(monkeypatch: pytest.MonkeyPatch, wcs: Any, **context_fields: Any) -> AstrometryQualitySummary:
    """Run `validate_output` on a canned context.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to stub the driver statistics the summary reads.
    wcs : `Any`
        The solved coordinate system, or `None` for an unsolved image.
    **context_fields
        Context fields to replace, such as the fit residual.

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
        catalog_match_separation_rms_arcsec=None,
        plate_solve_fit_residual_rms_arcsec=None,
        plate_solve_matched_star_count=None,
        astrometry_flags=[],
        wcs=wcs,
        image=SimpleNamespace(header={}),
    )
    for name, value in context_fields.items():
        setattr(context, name, value)
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


def _validate_with_measured_scale(
    monkeypatch: pytest.MonkeyPatch, **context_fields: Any
) -> AstrometryQualitySummary:
    """Run `validate_output` with a plate scale and star width on record.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to stub the measurements.
    **context_fields
        Context fields to replace, such as the fit residual.

    Returns
    -------
    summary : `AstrometryQualitySummary`
        The summary `validate_output` built.
    """
    monkeypatch.setattr(runner, "_plate_scale_arcsec_per_pixel", lambda wcs: 1.9)
    monkeypatch.setattr(runner, "_measure_star_fwhm_px", lambda image: 3.0)
    return _validate(monkeypatch, wcs=object(), **context_fields)


def test_the_summary_records_the_fit_residual_beside_the_catalog_separation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fit residual, star count and catalog proxy are separate."""
    summary = _validate_with_measured_scale(
        monkeypatch,
        plate_solve_fit_residual_rms_arcsec=0.6,
        plate_solve_matched_star_count=37,
        catalog_match_separation_rms_arcsec=4.2,
        astrometric_residual_rms_arcsec=4.2,
    )

    metrics = summary.astrometry_metrics
    assert metrics.plate_solve_fit_residual_rms_arcsec == pytest.approx(0.6)
    assert metrics.plate_solve_matched_star_count == 37
    assert metrics.catalog_match_separation_rms_arcsec == pytest.approx(4.2)
    gate = summary.gate("astrometric_residual")
    assert gate.status is GateStatus.PASSED
    assert "plate-solve fit residual" in gate.detail


def test_the_summary_gate_falls_back_to_the_catalog_separation_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no driver fit residual, the gate names the proxy it judged."""
    summary = _validate_with_measured_scale(
        monkeypatch, catalog_match_separation_rms_arcsec=1.2, astrometric_residual_rms_arcsec=1.2
    )

    gate = summary.gate("astrometric_residual")
    assert summary.astrometry_metrics.plate_solve_fit_residual_rms_arcsec is None
    assert gate.status is GateStatus.PASSED
    assert "catalog match separation" in gate.detail
    assert "reported no fit residual" in gate.detail
