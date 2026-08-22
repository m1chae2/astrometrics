"""Purpose: Guider Calibration Domain Model.

Description: The measured relationship between guide-camera pixels and
mount motion. Foundation state for the same reason equipment
specification is (`Wayfinding_Library_Architecture.md` §2.2.2): it
describes a measured, slowly changing physical relationship, not a
per-night observation. It is the precondition for autoguiding to enter
the shadowed delegation state -- without it there is no mapping from
measured drift to a comparable pulse.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class GuiderCalibration(BaseModel):
    """Measured pixel-to-mount-motion relation for one camera/telescope."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    camera_id: str
    telescope_id: str
    arcsec_per_pixel: float = Field(..., gt=0.0)
    camera_angle_deg: float = Field(..., ge=-180.0, le=180.0)
    ra_rate_arcsec_per_sec: float = Field(..., gt=0.0)
    dec_rate_arcsec_per_sec: float = Field(..., gt=0.0)
    calibrated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
