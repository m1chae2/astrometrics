"""Purpose: Fault Recovery Domain Models.

Description: How many times, and how far apart, a faulted device may be
recovered before the observatory is taken to a safe state. Recovery is
deliberately shallow -- it re-establishes a device's lifecycle state
and nothing more, because a system that cannot see the observatory
cannot distinguish a transient driver disconnection from a mechanical
obstruction (`Wayfinding_Library_Architecture.md` §2.4.6). Exhausting
the attempt bound escalates to the safe-state sequence rather than to a
deeper recovery strategy -- an unbounded retry is indistinguishable
from a hang, and a hang is precisely what unattended operation cannot
tolerate.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from wayfindinglib.models.policy.device_state import DeviceSummaryState


class RecoveryPolicy(BaseModel):
    """The bound on how a faulted device may be recovered."""

    model_config = ConfigDict(populate_by_name=True)

    max_attempts: int = Field(default=3, gt=0)
    initial_interval_sec: int = Field(default=30, gt=0)
    backoff_multiplier: float = Field(default=2.0, ge=1.0)

    def interval_for_attempt(self, attempt_number: int) -> float:
        """Return the wait interval, in seconds, before `attempt_number`.

        `attempt_number` is 1-indexed; the first attempt has no
        preceding wait.

        Returns
        -------
        interval_sec : `float`
            The wait interval, in seconds, before `attempt_number`.
        """
        if attempt_number <= 1:
            return 0.0
        return self.initial_interval_sec * (self.backoff_multiplier ** (attempt_number - 2))


class RecoveryAttempt(BaseModel):
    """One attempt to return a faulted device to standby and re-enable it."""

    model_config = ConfigDict(populate_by_name=True)

    attempted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    outcome: DeviceSummaryState
    detail: str = Field(default="")


class FaultRecord(BaseModel):
    """One device fault, its bounded recovery attempts, and its outcome."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    device_id: str
    faulted_state: DeviceSummaryState
    fault_detail: str = Field(default="")
    attempts: list[RecoveryAttempt] = Field(default_factory=list)
    recovered: bool = Field(default=False)
    escalated_to_safe_state: bool = Field(default=False)
