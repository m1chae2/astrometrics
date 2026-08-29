"""Purpose: Correction Algorithm Result Domain Models.

Description: The computed records returned by the three Observatory
Control correction algorithms (`Wayfinding_Library_Architecture.md`
§2.5.1's class diagram). Kept separate from `correction_config.py`,
which holds the tunable parameters these algorithms read rather than
anything they produce.

Per the "Corrections Are Pure" invariant (§2.5.9), computing one of
these records issues nothing to hardware -- issuing is a separate,
delegation-gated step layered on top. Each record is recorded through
the `DivergenceRecord` it contributes to while its capability is
shadowed, and through `GuidingSample` (pointing/guiding) while
authoritative (§2.5.10).
"""

from pydantic import BaseModel, ConfigDict, Field


class PointingCorrection(BaseModel):
    """One iteration of plate-solve pointing alignment (§2.5.3)."""

    model_config = ConfigDict(populate_by_name=True)

    comparison_input_id: str
    commanded_ra_deg: float = Field(..., ge=0.0, lt=360.0)
    commanded_dec_deg: float = Field(..., ge=-90.0, le=90.0)
    solved_ra_deg: float = Field(..., ge=0.0, lt=360.0)
    solved_dec_deg: float = Field(..., ge=-90.0, le=90.0)
    pointing_error_arcsec: float = Field(..., ge=0.0)
    correction_ra_arcsec: float
    correction_dec_arcsec: float
    iteration: int = Field(..., gt=0)
    converged: bool


class GuidingCorrection(BaseModel):
    """One computed guiding pulse pair from a measured drift (§2.5.4, Eq. 2).

    `pulse_ra_ms`/`pulse_dec_ms` are signed: the magnitude is the pulse
    duration, the sign names the direction (issuing maps sign to the
    `TELESCOPE_TIMED_GUIDE_NS`/`_WE` pair `pulse_guide` already
    writes), per §2.5.4's "signed per-axis pulse durations."
    """

    model_config = ConfigDict(populate_by_name=True)

    comparison_input_id: str
    drift_ra_arcsec: float
    drift_dec_arcsec: float
    pulse_ra_ms: int
    pulse_dec_ms: int
    aggressiveness_applied: float = Field(..., gt=0.0, le=1.0)
    suppressed_by_deadband: bool
    clamped_by_max_move: bool


class FocusCurvePoint(BaseModel):
    """One sampled (focuser position, measured star size) pair (§2.5.5)."""

    model_config = ConfigDict(populate_by_name=True)

    focuser_position: int
    measured_fwhm_px: float = Field(..., gt=0.0)
    star_count: int = Field(..., ge=0)


class FocusCorrection(BaseModel):
    """One autofocus run's sampled curve, fit, and selected position.

    Per `Wayfinding_Library_Architecture.md` §2.5.5.
    """

    model_config = ConfigDict(populate_by_name=True)

    comparison_input_id: str
    curve: list[FocusCurvePoint] = Field(default_factory=list)
    starting_position: int
    selected_position: int
    fitted_minimum_fwhm_px: float = Field(..., gt=0.0)
    fit_quality: float = Field(..., ge=0.0, le=1.0)
    trigger_reason: str
    converged: bool
