"""Purpose: Meridian Flip Outcome Domain Model.

Description: The result of one bounded interrupt-flip-reacquire-resume
sequence -- the single most failure-prone moment in an unattended night
(`Wayfinding_Library_Architecture.md` §2.4.5). Each step in the
sequence has an attempt bound; exhausting any of them produces
`resumed=False` with a `failure_detail` rather than a retry loop,
because a flip that cannot re-acquire is a night that must be ended
safely, not one that should keep trying while the target sets.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class MeridianFlipOutcome(BaseModel):
    """The outcome of one meridian-flip sequence for one queue entry."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    queued_observation_package_id: str
    triggered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    hour_angle_at_trigger_deg: float
    flip_completed: bool = Field(default=False)
    realign_attempts: int = Field(default=0, ge=0)
    residual_pointing_error_arcsec: float | None = Field(default=None)
    guide_reacquire_attempts: int = Field(default=0, ge=0)
    resumed: bool = Field(default=False)
    failure_detail: str | None = Field(default=None)
