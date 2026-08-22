"""Purpose: Unit tests for mosaic and quality advisory domain models.

Description: Verifies MosaicGridConfig/MosaicPanel construction and
TargetQualityAdvisory.has_any_flagged()'s aggregation across pipelines.
"""

import pytest

from wayfindinglib.models.planning.mosaic import MosaicGridConfig, MosaicPanel
from wayfindinglib.models.planning.quality_advisory import (
    QualityFlagSummary,
    ScienceOutcomeSummary,
    TargetQualityAdvisory,
)


def test_mosaic_grid_config_defaults():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify MosaicGridConfig's default overlap."""
    grid = MosaicGridConfig(rows=2, cols=3)
    assert grid.overlap_percent == pytest.approx(10.0)


def test_mosaic_panel_carries_resolved_target_reference():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify MosaicPanel references its panel sub-target by identifier."""
    panel = MosaicPanel(row=0, col=1, ra_deg=314.75, dec_deg=44.34, panel_target_id="NGC 7000-p0-1")
    assert panel.panel_target_id == "NGC 7000-p0-1"


def test_has_any_flagged_true_when_one_pipeline_flags():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify has_any_flagged() aggregates across all quality_flags entries."""
    advisory = TargetQualityAdvisory(
        target_id="M 81",
        quality_flags=[
            QualityFlagSummary(pipeline_name="astrometry", flagged=False),
            QualityFlagSummary(pipeline_name="stacking", flagged=True, flag_reasons=["low SNR"]),
        ],
    )
    assert advisory.has_any_flagged() is True


def test_has_any_flagged_false_when_none_flagged():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify has_any_flagged() is False when every pipeline is clean."""
    advisory = TargetQualityAdvisory(
        target_id="M 81",
        quality_flags=[QualityFlagSummary(pipeline_name="astrometry", flagged=False)],
    )
    assert advisory.has_any_flagged() is False


def test_science_outcomes_default_to_zero():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify ScienceOutcomeSummary defaults every count to zero."""
    outcomes = ScienceOutcomeSummary()
    assert outcomes.variable_star_candidate_count == 0
    assert outcomes.asteroid_candidate_count == 0
    assert outcomes.confirmed_asteroid_candidate_count == 0
