"""Red-team tests for the spectroscopy run gates.

Each gate has to fail on a run that is truly bad, pass on a good one, and
read "not checked" when it could not look. The last tests run the real
`validate_output` and the batch merge, which carry the gates into a saved
summary.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.models.gate_result import GateResult, GateStatus
from astrometricslib.models.target import Target
from astrometricslib.pipelines.pipeline_base import PipelineRequest, Result
from astrometricslib.pipelines.shared.quality.saturation import DEFAULT_SATURATION_FLAG_THRESHOLD
from astrometricslib.pipelines.shared.star_recording import StarIdentificationBreakdown
from astrometricslib.pipelines.spectroscopy import runner
from astrometricslib.pipelines.spectroscopy.post_processing import run_gates as rg


def make_star(
    spectral_type: str = "G2V",
    agrees: bool | None = True,
    methods: tuple[str, ...] = ("control_calibrated",),
    resolution: float | None = 12.0,
) -> SimpleNamespace:
    """Build a stand-in star with the spectrum fields the gates read.

    Returns
    -------
    star : `SimpleNamespace`
        A star whose ``spectroscopy`` carries those fields.
    """
    comparison = None if agrees is None else SimpleNamespace(spectral_type_agrees=agrees)
    return SimpleNamespace(
        spectroscopy=SimpleNamespace(
            self_determined_spectral_type=spectral_type,
            catalog_comparison=comparison,
            probable_spectral_features=[{"p_value_method": method} for method in methods],
            resolution_element_angstrom=resolution,
            dispersion_angle=0.0,
            trail_width_px=[3.0, 3.2],
        )
    )


def run_gates(
    stars: list[Any], zero_order: list[float] | None = None, concerns: list[dict] | None = None
) -> dict:
    """Build the gates for a set of stand-in stars.

    Returns
    -------
    gates : `dict` [`str`, `GateResult`]
        The gates keyed by name.
    """
    gates = rg.spectroscopy_run_gates(rg.spectrum_facts(stars), zero_order or [], concerns or [])
    return {gate.name: gate for gate in gates}


def test_a_healthy_run_passes_every_gate() -> None:
    """Good spectra give six passes and no failure."""
    gates = run_gates([make_star(), make_star("K0V")], zero_order=[0.0, 0.0])

    assert len(gates) == 6
    assert {gate.status for gate in gates.values()} == {GateStatus.PASSED}


def test_spectra_gate_goes_red_when_no_star_produced_a_spectrum() -> None:
    """An empty run fails; it must not read as clean."""
    gates = run_gates([])

    assert gates[rg.SPECTRA_GATE_NAME].status is GateStatus.FAILED
    assert gates[rg.SPECTRA_GATE_NAME].detail == "no star produced a spectrum"


def test_zero_order_gate_goes_red_when_the_zero_order_is_saturated() -> None:
    """One percent saturated fails; none passes; unmeasured is not checked."""
    failed = run_gates([make_star()], zero_order=[0.01])[rg.ZERO_ORDER_GATE_NAME]
    assert failed.status is GateStatus.FAILED
    assert failed.limit == pytest.approx(DEFAULT_SATURATION_FLAG_THRESHOLD)

    assert run_gates([make_star()], zero_order=[0.0])[rg.ZERO_ORDER_GATE_NAME].status is GateStatus.PASSED
    assert run_gates([make_star()], zero_order=[])[rg.ZERO_ORDER_GATE_NAME].status is GateStatus.NOT_CHECKED


def test_classification_gate_goes_red_for_shaky_types_and_is_unchecked_without_any() -> None:
    """A concern fails; no classified star is not checked."""
    concern = {"star_id": "A", "reason": "poor_match"}
    failed = run_gates([make_star()], concerns=[concern])[rg.CLASSIFICATION_GATE_NAME]
    assert failed.status is GateStatus.FAILED
    assert failed.detail.startswith("spectral classification uncertain for 1 star(s)")

    unchecked = run_gates([make_star("Unknown"), make_star("")])[rg.CLASSIFICATION_GATE_NAME]
    assert unchecked.status is GateStatus.NOT_CHECKED


def test_classification_gate_fails_for_each_reason_the_limits_define() -> None:
    """A poor match or class tie fails the gate; a subtype-only tie does not.

    The stars go through `build_spectral_classification_concerns`, so the
    gate reads the same limits (`NO_GOOD_MATCH_RMS` and `AMBIGUOUS_RMS_GAP`)
    as the star's own fields. A subtype-only tie is reported in the gate's
    detail but leaves it passed, and a clear match passes too.
    """
    from astrometricslib.models.stellar_source import (
        AMBIGUOUS_RMS_GAP,
        NO_GOOD_MATCH_RMS,
        SpectroscopyResult,
        StellarObject,
    )
    from astrometricslib.pipelines.spectroscopy.post_processing.assess_output_quality import (
        build_spectral_classification_concerns,
    )

    def star(best: float, second: float, second_type: str = "M0V") -> StellarObject:
        """Build a classified K5V star with the given best and second RMS.

        Returns
        -------
        star : `StellarObject`
            The star.
        """
        return StellarObject(
            id="S",
            spectroscopy=SpectroscopyResult(
                self_determined_spectral_type="K5V",
                self_determined_spectral_type_rms=best,
                self_determined_spectral_type_candidates=[
                    {"spectral_type": "K5V", "rms": best},
                    {"spectral_type": second_type, "rms": second},
                ],
            ),
        )

    poor_star = star(NO_GOOD_MATCH_RMS + 0.05, 0.5)
    class_tie = star(0.05, 0.05 + AMBIGUOUS_RMS_GAP / 2)
    subtype_tie = star(0.05, 0.05 + AMBIGUOUS_RMS_GAP / 2, second_type="K7V")
    clear = star(0.05, 0.05 + 2 * AMBIGUOUS_RMS_GAP)

    for failing in (poor_star, class_tie):
        concerns = build_spectral_classification_concerns([failing])
        gate = run_gates([failing], concerns=concerns)[rg.CLASSIFICATION_GATE_NAME]
        assert gate.status is GateStatus.FAILED
    for passing in (subtype_tie, clear):
        concerns = build_spectral_classification_concerns([passing])
        assert concerns == []
        gate = run_gates([passing], concerns=concerns)[rg.CLASSIFICATION_GATE_NAME]
        assert gate.status is GateStatus.PASSED
    subtype_gate = run_gates([subtype_tie])[rg.CLASSIFICATION_GATE_NAME]
    assert "1 of 1 classifications ambiguous at subtype level" in subtype_gate.detail
    clear_gate = run_gates([clear])[rg.CLASSIFICATION_GATE_NAME]
    assert "0 of 1 classifications ambiguous at subtype level" in clear_gate.detail


def test_catalog_gate_goes_red_on_disagreement_and_is_unchecked_without_a_catalog_type() -> None:
    """A disagreeing star fails; stars with no catalog type are not checked."""
    failed = run_gates([make_star(agrees=True), make_star(agrees=False)])[rg.CATALOG_GATE_NAME]
    assert failed.status is GateStatus.FAILED
    assert "1 of 2" in failed.detail

    unchecked = run_gates([make_star(agrees=None)])[rg.CATALOG_GATE_NAME]
    assert unchecked.status is GateStatus.NOT_CHECKED


def test_feature_gate_goes_red_when_a_p_value_fell_back_to_gaussian() -> None:
    """A Gaussian fallback fails; no tested features is not checked."""
    failed = run_gates([make_star(methods=("control_calibrated", "gaussian"))])[rg.FEATURE_GATE_NAME]
    assert failed.status is GateStatus.FAILED

    unchecked = run_gates([make_star(methods=())])[rg.FEATURE_GATE_NAME]
    assert unchecked.status is GateStatus.NOT_CHECKED


def test_resolution_gate_is_not_checked_when_it_was_assumed_for_every_spectrum() -> None:
    """An assumed resolution is on record as not measured, not as a pass."""
    gate = run_gates([make_star(resolution=None), make_star(resolution=None)])[rg.RESOLUTION_GATE_NAME]

    assert gate.status is GateStatus.NOT_CHECKED


def test_resolution_gate_names_how_many_spectra_were_measured() -> None:
    """A partly measured run passes and says how many."""
    gate = run_gates([make_star(resolution=10.0), make_star(resolution=None)])[rg.RESOLUTION_GATE_NAME]

    assert gate.status is GateStatus.PASSED
    assert "1 of 2" in gate.detail


def test_stars_without_a_spectrum_are_not_counted() -> None:
    """A star that has no spectroscopy result adds nothing."""
    facts = rg.spectrum_facts([SimpleNamespace(spectroscopy=None), make_star()])

    assert facts["spectra"] == 1


def test_batch_merge_adds_counts_and_treats_missing_as_zero() -> None:
    """Frames that carried no counts add zero."""
    merged = rg.merge_spectrum_facts([{"spectra": 2, "classified": 1}, None, {"spectra": 3}])

    assert merged["spectra"] == 5
    assert merged["classified"] == 1
    assert merged["catalog_disagree"] == 0


def test_the_real_validate_output_records_the_gates() -> None:
    """The runner puts the gates on the summary and flags what failed."""
    stars = [make_star(agrees=False)]
    spectroscopy = SimpleNamespace(
        last_run_zero_order_saturation_fractions=[0.02],
        config=SimpleNamespace(camera=SimpleNamespace(name="ZWO ASI 533MM Pro")),
    )
    result = Result(
        stellar_objects=stars,
        payload={
            "star_id_breakdown": StarIdentificationBreakdown(
                catalog_matched=1, position_only=0, unresolved=0
            ),
            "spectroscopy": spectroscopy,
            "flagged_spectral_classifications": [],
        },
    )
    request = PipelineRequest(target=Target(id="Test Target"), catalog_access=None)

    summary = runner.SpectroscopyPipelineAdapter().validate_output(request, result)

    assert summary.flagged is True
    assert "zero-order saturated in at least one processed star" in summary.flag_reasons
    assert summary.gate(rg.ZERO_ORDER_GATE_NAME).status is GateStatus.FAILED
    assert summary.gate(rg.CATALOG_GATE_NAME).status is GateStatus.FAILED
    assert summary.gate(rg.SPECTRA_GATE_NAME).status is GateStatus.PASSED


def test_gates_are_distinct_results() -> None:
    """Every gate returned is a `GateResult` with its own name."""
    gates = rg.spectroscopy_run_gates(rg.spectrum_facts([make_star()]), [0.0], [])

    assert all(isinstance(gate, GateResult) for gate in gates)
    assert len({gate.name for gate in gates}) == len(gates)


def test_a_classification_concern_fails_the_gate_even_without_a_classified_count() -> None:
    """A concern proves classification ran, so it fails rather than skips."""
    facts = rg.spectrum_facts([])
    facts["spectra"] = 1
    gates = {
        gate.name: gate
        for gate in rg.spectroscopy_run_gates(facts, [0.0], [{"star_id": "A", "reason": "ambiguous"}])
    }

    assert gates[rg.CLASSIFICATION_GATE_NAME].status is GateStatus.FAILED
