"""Purpose: Guide-Star Loss Event Domain Model.

Description: One episode where this system's own guiding, under
`AUTHORITATIVE` control, could not measure a valid star-centroid drift
for a guide frame, and the bounded reacquisition attempted in response
(`Wayfinding_Library_Architecture.md` §2.4.6's "recovery is
deliberately shallow" precedent, applied here to guiding rather than
device lifecycle). Distinct from `MeridianFlipOutcome
.guide_reacquire_attempts` -- that field is a bounded reacquisition
triggered specifically by a meridian flip; this model covers a standing
loss during otherwise normal guiding, the case the commissioning gate
criterion "zero unrecovered guide-star losses" actually measures.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class GuideStarLossEvent(BaseModel):
    """One guide-star loss and its bounded reacquisition attempts."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    observation_session_id: str
    comparison_input_id: str
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reacquire_attempts: int = Field(default=0, ge=0)
    recovered: bool = Field(default=False)
