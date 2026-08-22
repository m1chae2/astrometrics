"""Purpose: Safe-State Outcome Domain Model.

Description: The record of one execution of the ordered, bounded
safe-state sequence: abandon exposure, stop guiding, park mount, close
enclosure, warm sensor, close session
(`Wayfinding_Library_Architecture.md` §2.5.5, Table 6). Each step is
attempted even if an earlier one failed, except enclosure closure, which
is *skipped* rather than forced when the mount did not reach a
clearance position -- forcing it is the damage case.
`failed_step` records the first failure, so a partially completed safe
state is diagnosable rather than an unknown condition.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class SafeStateOutcome(BaseModel):
    """The per-step outcome of one safe-state sequence execution."""

    model_config = ConfigDict(populate_by_name=True)

    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    trigger: str = Field(description='e.g. "unsafe_verdict", "recovery_exhausted", "watchdog".')
    exposure_abandoned: bool = Field(default=False)
    guiding_stopped: bool = Field(default=False)
    mount_parked: bool = Field(default=False)
    enclosure_closed: bool = Field(default=False)
    sensor_warmed: bool = Field(default=False)
    session_closed: bool = Field(default=False)
    failed_step: str | None = Field(default=None)
