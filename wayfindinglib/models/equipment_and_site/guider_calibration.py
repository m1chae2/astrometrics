"""Purpose: Guider Calibration Domain Model.

Description: The measured relationship between guide-camera pixels and
mount motion. Foundation state for the same reason equipment
specification is (`Wayfinding_Library_Architecture.md` Section 3.2): it
describes a measured, slowly changing physical relationship, not a
per-night observation. It is the precondition for autoguiding to enter
the shadowed delegation state -- without it there is no mapping from
measured drift to a comparable pulse.
"""

from datetime import UTC, datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class GuiderCalibration(BaseModel):
    """Measured pixel-to-mount-motion relation for one camera/telescope."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    camera_id: str
    telescope_id: str
    arcsec_per_pixel: float = Field(..., gt=0.0)
    camera_angle_deg: float = Field(..., ge=-180.0, le=180.0)
    ra_rate_arcsec_per_s: float = Field(
        ..., gt=0.0, validation_alias=AliasChoices("ra_rate_arcsec_per_s", "ra_rate_arcsec_per_sec")
    )
    dec_rate_arcsec_per_s: float = Field(
        ..., gt=0.0, validation_alias=AliasChoices("dec_rate_arcsec_per_s", "dec_rate_arcsec_per_sec")
    )
    calibrated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_note: str = Field(
        default="", description="Where the numbers came from; a simulator's made-up values say so here."
    )
