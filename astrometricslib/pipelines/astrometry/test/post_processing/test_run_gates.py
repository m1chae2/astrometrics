"""Red-team tests for the astrometry source-detection and catalog gates.

Each gate has to fail on a run that is truly bad, pass on a good one, and
read "not checked" when it could not look.
"""

from astrometricslib.models.gate_result import GateStatus
from astrometricslib.models.quality_summary import AstrometryPipelineQualityMetrics
from astrometricslib.pipelines.astrometry.post_processing import run_gates as rg


def make_metrics(**changes: object) -> AstrometryPipelineQualityMetrics:
    """Build the metrics of a healthy run, with some fields changed.

    Parameters
    ----------
    **changes
        Fields to replace.

    Returns
    -------
    metrics : `AstrometryPipelineQualityMetrics`
        A run that found 80 stars and made 10 lookups, none failed.
    """
    fields: dict[str, object] = {
        "sources_detected": 80,
        "solve_attempted": True,
        "plate_solve_succeeded": True,
        "simbad_matched_count": 40,
        "remote_catalog_queries_attempted": 10,
        "remote_catalog_queries_failed": 0,
        "remote_catalog_circuit_breaker_tripped": False,
    }
    fields.update(changes)
    return AstrometryPipelineQualityMetrics(**fields)


def gates_for(**changes: object) -> dict:
    """Build the gates for a run with some metrics changed.

    Returns
    -------
    gates : `dict` [`str`, `GateResult`]
        The gates keyed by name.
    """
    return {gate.name: gate for gate in rg.astrometry_run_gates(make_metrics(**changes))}


def test_a_healthy_run_passes_both_gates() -> None:
    """Stars found and lookups working give two passes."""
    gates = gates_for()

    assert {gate.status for gate in gates.values()} == {GateStatus.PASSED}


def test_source_detection_gate_goes_red_when_no_star_was_found() -> None:
    """An image with no detected star fails and says so."""
    gate = gates_for(sources_detected=0)[rg.SOURCE_DETECTION_GATE_NAME]

    assert gate.status is GateStatus.FAILED
    assert gate.detail == "no stars were detected in the image"


def test_catalog_gate_goes_red_when_the_circuit_breaker_tripped() -> None:
    """Giving up on a catalog service fails the gate."""
    gate = gates_for(remote_catalog_circuit_breaker_tripped=True)[rg.CATALOG_LOOKUP_GATE_NAME]

    assert gate.status is GateStatus.FAILED


def test_catalog_gate_goes_red_when_half_the_lookups_failed() -> None:
    """Five of ten lookups failing is at the limit, so it fails."""
    gate = gates_for(remote_catalog_queries_failed=5)[rg.CATALOG_LOOKUP_GATE_NAME]

    assert gate.status is GateStatus.FAILED
    assert "5 of 10" in gate.detail


def test_catalog_gate_tolerates_an_occasional_failed_lookup() -> None:
    """One failure in ten passes, and the failure is on record."""
    gate = gates_for(remote_catalog_queries_failed=1)[rg.CATALOG_LOOKUP_GATE_NAME]

    assert gate.status is GateStatus.PASSED
    assert gate.detail == "1 of 10 catalog lookup(s) failed"


def test_catalog_gate_is_not_checked_when_no_lookup_was_attempted() -> None:
    """No lookup means no check; that is not a pass."""
    gate = gates_for(remote_catalog_queries_attempted=0)[rg.CATALOG_LOOKUP_GATE_NAME]

    assert gate.status is GateStatus.NOT_CHECKED


def test_a_breaker_that_was_already_open_means_no_lookup_was_made_not_a_failure() -> None:
    """With the breaker open and no lookup attempted, the run did not fail."""
    gate = gates_for(
        remote_catalog_circuit_breaker_tripped=True,
        remote_catalog_queries_attempted=0,
        remote_catalog_queries_failed=0,
    )[rg.CATALOG_LOOKUP_GATE_NAME]

    assert gate.status is GateStatus.NOT_CHECKED
    assert "already marked unreachable" in gate.detail
