"""Purpose: Correction Algorithm Configuration Domain Model.

Description: Design-estimate parameters for the pointing, guiding, and
focus correction algorithms, and the divergence tolerances the delegation
phase gates (`Wayfinding_Library_Architecture.md` Table 6) are
evaluated against. None has been validated against a full observing
night -- the guiding and focus parameters in particular are expected to
be revised from the first real distribution of Phase 2 divergence data
(values in `Wayfinding_Library_Implementation.md` Table 7; the gates are
design estimates, per `Wayfinding_Library_Architecture.md` Section 7.2).
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
    # Exposure of a centering frame when the equipment's measurements are not
    # used or cannot give one (`tasks.control_tasks.centering_exposure`). 5 s:
    # the shortest ladder step whose star count the model puts well above the
    # solver's 10 for a 75 mm f/5.4 telescope with the ASI533MM Pro at gain 0
    # under a dark sky (about 1.9 arcsec per pixel, 1.6 degree field). Design
    # estimate: the model uses a photometric zero point that is still being
    # checked (issue #161); the real-data check is register entry WAY-D122.
    alignment_exposure_seconds: float = Field(default=5.0, gt=0.0)
    # Off until the exposure model passes its real-data check (WAY-D122,
    # issue #161). Until then centering uses `alignment_exposure_seconds`.
    alignment_exposure_from_equipment: bool = False

    # Focus
    focus_sample_count: int = Field(default=9, gt=0)
    focus_sample_span_steps: int = Field(default=2000, gt=0)
    focus_temperature_delta_trigger_c: float = Field(default=1.0, gt=0.0)
    focus_fit_quality_floor: float = Field(default=0.90, ge=0.0, le=1.0)
    focus_divergence_tolerance_steps: int = Field(default=50, gt=0)

    # Meridian flip
    guide_reacquire_attempts: int = Field(default=3, gt=0)
