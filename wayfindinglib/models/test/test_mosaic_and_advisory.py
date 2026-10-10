"""Purpose: Unit tests for mosaic and quality advisory domain models.

Description: Verifies MosaicPanel range checks and
TargetQualityAdvisory.has_any_flagged()'s aggregation across pipelines.
"""

import pytest
from pydantic import ValidationError

from wayfindinglib.models.planning.mosaic import MosaicPanel
from wayfindinglib.models.planning.quality_advisory import (
    QualityFlagSummary,
    ScienceOutcomeSummary,
    TargetQualityAdvisory,
)


def test_mosaic_panel_checks_its_position() -> None:
    """A panel keeps its id and refuses a declination past the pole."""
    panel = MosaicPanel(
        row=0,
        col=1,
        ra_str="20 59 00.0",
        dec_str="+44 20 24.0",
        ra_deg=314.75,
        dec_deg=44.34,
        panel_id="P1_2",
    )
    assert panel.panel_id == "P1_2"
    with pytest.raises(ValidationError):
        MosaicPanel(row=0, col=1, ra_str="", dec_str="", ra_deg=10.0, dec_deg=91.0, panel_id="P1_2")


def test_has_any_flagged_true_when_one_pipeline_flags() -> None:
    """Verify has_any_flagged() aggregates across all quality_flags entries."""
    advisory = TargetQualityAdvisory(
        target_id="M 81",
        quality_flags=[
            QualityFlagSummary(pipeline_name="astrometry", flagged=False),
            QualityFlagSummary(pipeline_name="stacking", flagged=True, flag_reasons=["low SNR"]),
        ],
    )
    assert advisory.has_any_flagged() is True


def test_has_any_flagged_false_when_none_flagged() -> None:
    """Verify has_any_flagged() is False when every pipeline is clean."""
    advisory = TargetQualityAdvisory(
        target_id="M 81",
        quality_flags=[QualityFlagSummary(pipeline_name="astrometry", flagged=False)],
    )
    assert advisory.has_any_flagged() is False


def test_science_outcomes_default_to_zero() -> None:
    """Verify ScienceOutcomeSummary defaults every count to zero."""
    outcomes = ScienceOutcomeSummary()
    assert outcomes.variable_star_candidate_count == 0
    assert outcomes.asteroid_candidate_count == 0
    assert outcomes.confirmed_asteroid_candidate_count == 0
