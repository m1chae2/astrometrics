"""Purpose: Observation Planning Configuration Domain Model.

Description: Design-estimate parameters governing night-window
resolution, advisory priority boosts, and mosaic generation. None has
been validated against a full observing night
(`Wayfinding_Library_Architecture.md` Appendix A) -- each is a design
estimate rather than a tuned value, with the exception of the twilight
threshold, which is a standard astronomical definition.
"""

from pydantic import BaseModel, ConfigDict, Field


class PlanningConfig(BaseModel):
    """Configuration parameters for the Observation Planning subsystem."""

    model_config = ConfigDict(populate_by_name=True)

    night_window_time_step_min: float = Field(default=5.0, gt=0.0)
    twilight_sun_altitude_deg: float = Field(
        default=-12.0, description="Nautical twilight threshold; a standard astronomical definition."
    )
    flagged_quality_priority_boost: int = Field(default=2, ge=0)
    science_outcome_priority_boost: int = Field(default=1, ge=0)
    dither_every_n_frames_default: int = Field(default=3, gt=0)
    dither_pixels_default: float = Field(default=3.0, gt=0.0)
    mosaic_panel_overlap_percent: float = Field(default=10.0, ge=0.0, lt=100.0)
