"""Red-team tests for the astrometry source-detection and catalog gates.

Each gate has to fail on a run that is truly bad, pass on a good one, and
read "not checked" when it could not look.
"""

import pytest

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
        "catalog_matched_star_count": 150,
        "astrometric_residual_rms_arcsec": 1.0,
        "plate_scale_arcsec_per_pixel": 1.9,
        "star_fwhm_px": 3.0,
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
    """A good solve, stars found and lookups working give all passes."""
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


def test_the_residual_gate_goes_red_when_the_fit_misses_by_more_than_half_a_star() -> None:
    """A 5 arcsec residual with 1.9 arcsec pixels and 3 pixel stars is 88%."""
    gate = gates_for(astrometric_residual_rms_arcsec=5.0)[rg.RESIDUAL_GATE_NAME]

    assert gate.status is GateStatus.FAILED
    assert gate.measured_value == pytest.approx(5.0 / (1.9 * 3.0))
    assert "may be wrong" in gate.detail


def test_the_residual_gate_passes_a_good_fit_with_the_ratio_on_record() -> None:
    """A 1 arcsec residual is 18% of a star's width."""
    gate = gates_for()[rg.RESIDUAL_GATE_NAME]

    assert gate.status is GateStatus.PASSED
    assert gate.measured_value == pytest.approx(1.0 / (1.9 * 3.0))


@pytest.mark.parametrize(
    "missing",
    [
        {"astrometric_residual_rms_arcsec": None},
        {"plate_scale_arcsec_per_pixel": None},
        {"star_fwhm_px": None},
    ],
)
def test_the_residual_gate_is_not_checked_when_a_number_it_needs_is_missing(missing: dict) -> None:
    """Without the residual, plate scale or star width, the fit is unjudged."""
    assert gates_for(**missing)[rg.RESIDUAL_GATE_NAME].status is GateStatus.NOT_CHECKED


def test_the_matches_gate_goes_red_when_too_few_stars_were_matched() -> None:
    """Nineteen matched stars is one short of the minimum."""
    gate = gates_for(catalog_matched_star_count=19)[rg.MATCHED_STARS_GATE_NAME]

    assert gate.status is GateStatus.FAILED
    assert "only 19 star(s)" in gate.detail
    assert gates_for(catalog_matched_star_count=20)[rg.MATCHED_STARS_GATE_NAME].status is GateStatus.PASSED


def test_the_matches_gate_is_not_checked_when_the_image_was_not_solved() -> None:
    """An unsolved image has no matches to count; plate_solve reports it."""
    gate = gates_for(plate_solve_succeeded=False, catalog_matched_star_count=0)[rg.MATCHED_STARS_GATE_NAME]

    assert gate.status is GateStatus.NOT_CHECKED


def test_the_residual_gate_judges_the_plate_solve_fit_when_the_solver_gave_one() -> None:
    """A good fit passes even when the catalog separation alone would fail."""
    gate = gates_for(
        plate_solve_fit_residual_rms_arcsec=0.5,
        plate_solve_matched_star_count=42,
        catalog_match_separation_rms_arcsec=8.0,
        astrometric_residual_rms_arcsec=8.0,
    )[rg.RESIDUAL_GATE_NAME]

    assert gate.status is GateStatus.PASSED
    assert gate.measured_value == pytest.approx(0.5 / (1.9 * 3.0))
    assert "plate-solve fit residual" in gate.detail
    assert "42 matched stars" in gate.detail
    assert "catalog match separation" not in gate.detail


def test_the_residual_gate_fails_a_bad_fit_even_when_the_catalog_separation_is_small() -> None:
    """A fit that misses by 5 arcsec fails, whatever the catalog proxy says."""
    gate = gates_for(
        plate_solve_fit_residual_rms_arcsec=5.0,
        catalog_match_separation_rms_arcsec=1.0,
        astrometric_residual_rms_arcsec=1.0,
    )[rg.RESIDUAL_GATE_NAME]

    assert gate.status is GateStatus.FAILED
    assert gate.measured_value == pytest.approx(5.0 / (1.9 * 3.0))
    assert "plate-solve fit residual" in gate.detail


def test_the_residual_gate_falls_back_to_the_catalog_separation_and_says_so() -> None:
    """With no fit residual, the catalog proxy is judged and named."""
    passed = gates_for(catalog_match_separation_rms_arcsec=1.0)[rg.RESIDUAL_GATE_NAME]
    failed = gates_for(catalog_match_separation_rms_arcsec=5.0)[rg.RESIDUAL_GATE_NAME]

    assert passed.status is GateStatus.PASSED
    assert "catalog match separation" in passed.detail
    assert "reported no fit residual" in passed.detail
    assert failed.status is GateStatus.FAILED
    assert "catalog match separation" in failed.detail
    assert "reported no fit residual" in failed.detail


def test_the_residual_gate_reads_the_older_field_when_the_new_proxy_is_absent() -> None:
    """A summary saved before the rename still gives the gate its proxy."""
    gate = gates_for(catalog_match_separation_rms_arcsec=None, astrometric_residual_rms_arcsec=5.0)[
        rg.RESIDUAL_GATE_NAME
    ]

    assert gate.status is GateStatus.FAILED
    assert "catalog match separation" in gate.detail


def test_the_residual_gate_is_not_checked_with_neither_a_fit_nor_a_catalog_separation() -> None:
    """No fit residual and no catalog match leave nothing to judge."""
    gate = gates_for(
        plate_solve_fit_residual_rms_arcsec=None,
        catalog_match_separation_rms_arcsec=None,
        astrometric_residual_rms_arcsec=None,
    )[rg.RESIDUAL_GATE_NAME]

    assert gate.status is GateStatus.NOT_CHECKED
