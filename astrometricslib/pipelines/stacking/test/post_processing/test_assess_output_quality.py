"""Purpose: Unit tests for the stacking output-quality gates.

Description: The builder reads the measurements made on a finished stack and
builds one gate for each. A gate past its limit fails with a sentence; a stack
with nothing past a limit has no failed gate.
"""

from astrometricslib.models.quality.gate_result import GateResult, GateStatus
from astrometricslib.models.quality.quality_summary import ExcludedFrame, StackingPipelineQualityMetrics
from astrometricslib.pipelines.stacking.post_processing.assess_output_quality import output_quality_gates


def make_metrics() -> StackingPipelineQualityMetrics:
    """Build metrics with nothing measured.

    Returns
    -------
    metrics : `StackingPipelineQualityMetrics`
        Default metrics for a ten-frame image stack.
    """
    return StackingPipelineQualityMetrics(is_spectral=False, frames_submitted=10, frames_stacked=10)


def failed_sentences(gates: list[GateResult]) -> list[str]:
    """List the sentences of the failed gates.

    Parameters
    ----------
    gates : `list` [`GateResult`]
        The gates.

    Returns
    -------
    sentences : `list` [`str`]
        The detail of each failed gate.
    """
    return [gate.detail for gate in gates if gate.status is GateStatus.FAILED]


def test_a_stack_with_nothing_measured_has_no_failed_gate() -> None:
    """Nothing past a limit gives no failed gate."""
    assert failed_sentences(output_quality_gates(make_metrics())) == []


def test_each_failed_threshold_gives_one_failed_gate() -> None:
    """Rejected, zero and negative-pixel limits each fail their own gate."""
    metrics = make_metrics()
    metrics.rejected_pixel_fraction = 0.3
    metrics.zero_pixel_fraction = 0.9
    metrics.negative_pixel_max_percent = 60
    sentences = failed_sentences(output_quality_gates(metrics))
    assert len(sentences) == 3
    assert any("exactly zero" in sentence for sentence in sentences)


def test_spectral_registration_concerns_fail_their_gate() -> None:
    """Questioned spectral frames are worded in the registration gate."""
    metrics = make_metrics()
    metrics.is_spectral = True
    metrics.spectral_registration_checked = True
    metrics.spectral_registration_flags = [ExcludedFrame(path="a.fits", reason="moved")]
    assert failed_sentences(output_quality_gates(metrics)) == [
        "1 frame(s) with spectral registration concerns"
    ]
