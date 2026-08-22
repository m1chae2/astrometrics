"""Purpose: Unit tests for planning and correction configuration models.

Description: Verifies PlanningConfig and CorrectionConfig construct with
the documented default values (`Wayfinding_Library_Architecture.md`
Appendix A).
"""

import pytest

from wayfindinglib.models.planning.planning_config import PlanningConfig
from wayfindinglib.models.session.correction_config import CorrectionConfig


def test_planning_config_defaults_match_documented_values():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify PlanningConfig's defaults match Appendix A exactly."""
    config = PlanningConfig()
    assert config.night_window_time_step_min == pytest.approx(5.0)
    assert config.twilight_sun_altitude_deg == pytest.approx(-12.0)
    assert config.flagged_quality_priority_boost == 2
    assert config.science_outcome_priority_boost == 1
    assert config.dither_every_n_frames_default == 3
    assert config.dither_pixels_default == pytest.approx(3.0)
    assert config.mosaic_panel_overlap_percent == pytest.approx(10.0)


def test_correction_config_defaults_match_documented_values():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify CorrectionConfig's defaults match Appendix A exactly."""
    config = CorrectionConfig()
    assert config.guiding_aggressiveness == pytest.approx(0.7)
    assert config.guiding_deadband_arcsec == pytest.approx(0.15)
    assert config.guiding_max_pulse_ms == 1000
    assert config.guiding_divergence_tolerance_arcsec == pytest.approx(0.5)
    assert config.alignment_convergence_tolerance_arcsec == pytest.approx(30.0)
    assert config.alignment_iteration_limit == 3
    assert config.alignment_divergence_tolerance_arcsec == pytest.approx(5.0)
    assert config.focus_sample_count == 9
    assert config.focus_sample_span_steps == 2000
    assert config.focus_temperature_delta_trigger_c == pytest.approx(1.0)
    assert config.focus_fit_quality_floor == pytest.approx(0.90)
    assert config.focus_divergence_tolerance_steps == 50
    assert config.guide_reacquire_attempts == 3
