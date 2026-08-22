"""Purpose: Correction Algorithm Configuration Domain Model.

Description: Design-estimate parameters for the pointing, guiding, and
focus correction algorithms, and the divergence tolerances the delegation
phase gates (`Wayfinding_Library_Architecture.md` §3 Table 7) are
evaluated against. None has been validated against a full observing
night -- the guiding and focus parameters in particular are expected to
be revised from the first real distribution of Phase 2 divergence data
(`Wayfinding_Library_Architecture.md` Appendix A).
"""

from pydantic import BaseModel, ConfigDict, Field


class CorrectionConfig(BaseModel):
    """Configuration parameters for pointing, guiding, and focus correction."""

    model_config = ConfigDict(populate_by_name=True)

    # Guiding
    guiding_aggressiveness: float = Field(default=0.7, gt=0.0, le=1.0)
    guiding_deadband_arcsec: float = Field(default=0.15, ge=0.0)
    guiding_max_pulse_ms: int = Field(default=1000, gt=0)
    guiding_divergence_tolerance_arcsec: float = Field(default=0.5, gt=0.0)

    # Alignment
    alignment_convergence_tolerance_arcsec: float = Field(default=30.0, gt=0.0)
    alignment_iteration_limit: int = Field(default=3, gt=0)
    alignment_divergence_tolerance_arcsec: float = Field(default=5.0, gt=0.0)

    # Focus
    focus_sample_count: int = Field(default=9, gt=0)
    focus_sample_span_steps: int = Field(default=2000, gt=0)
    focus_temperature_delta_trigger_c: float = Field(default=1.0, gt=0.0)
    focus_fit_quality_floor: float = Field(default=0.90, ge=0.0, le=1.0)
    focus_divergence_tolerance_steps: int = Field(default=50, gt=0)

    # Meridian flip
    guide_reacquire_attempts: int = Field(default=3, gt=0)
