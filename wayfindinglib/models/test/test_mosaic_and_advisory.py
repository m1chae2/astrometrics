"""Purpose: Unit tests for the mosaic and science outcome domain models.

Description: Verifies MosaicPanel range checks and the defaults of
ScienceOutcomeSummary, the part of the target dataset report that Planning
reads to raise a package's priority.
"""

import pytest
from pydantic import ValidationError

from wayfindinglib.models.planning.mosaic import MosaicPanel
from wayfindinglib.models.planning.target_report import ScienceOutcomeSummary


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


def test_science_outcomes_default_to_zero() -> None:
    """Verify ScienceOutcomeSummary defaults every count to zero."""
    outcomes = ScienceOutcomeSummary()
    assert outcomes.variable_star_candidate_count == 0
    assert outcomes.asteroid_candidate_count == 0
    assert outcomes.confirmed_asteroid_candidate_count == 0
    assert outcomes.flagged_pipelines == []
