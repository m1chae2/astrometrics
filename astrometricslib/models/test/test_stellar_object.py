"""Tests for the star and analysis result structures.

Checks that the text summary of a processing job is generated correctly.
"""

from astrometricslib.models.stellar_source import AnalysisResult, VariableCandidate


def test_analysis_result_summary() -> None:
    """Make sure the job summary creates a readable report with the numbers."""
    candidate = VariableCandidate(
        id="star_123", meanFlux=5000.0, coefficientOfVariation=0.02, score=0.95, ra=120.5, dec=45.2
    )

    result = AnalysisResult(
        status="completed",
        targetId="M_42",
        totalImages=10,
        analysisMode="photometry",
        starsProcessed=150,
        spectraExtracted=0,
        starsFound=180,
        framesProcessed=10,
        rejectedCount=1,
        rejectedFiles=["frame_05.fits"],
        variableCandidates=[candidate],
        error=None,
        message="Variability analysis finished successfully.",
    )

    summary_output = result.summary
    assert "Analysis Target: M_42" in summary_output
    assert "Status: COMPLETED" in summary_output
    assert "Mode: photometry" in summary_output
    assert "Total Images: 10" in summary_output
    assert "Frames Processed: 10" in summary_output
    assert "Rejected Files: 1" in summary_output
    assert "Stars Found: 180" in summary_output
    assert "Stars Processed: 150" in summary_output
    assert "Variable Star Candidates: 1" in summary_output
