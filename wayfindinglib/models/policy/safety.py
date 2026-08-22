"""Purpose: Environmental Safety Domain Models.

Description: The environmental thresholds a running safety monitor
evaluates against, and the resulting continuously-refreshed verdict.
Three properties are architectural rather than incidental
(`Wayfinding_Library_Architecture.md` §2.5.4):

- Absence of information is not safety -- a stale, absent, or
  unparseable reading yields `UNKNOWN`, which `permits_observing()`
  treats exactly as `UNSAFE`. This deliberately reverses this library's
  general best-effort tolerance for missing telemetry (`WeatherSample`);
  that tolerance applies to the session record, never the safety
  verdict.
- Reopening is not the inverse of closing -- enforced by the safety
  monitor's asymmetric-hysteresis logic (`Wayfinding_Library_Architecture.md`
  §2.5.6), not by this schema.
- Safety authority is not observing authority -- `SafetyAssessment` is
  published continuously, whether or not a session exists.
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SafetyVerdict(StrEnum):
    """The current environmental judgment.

    `UNKNOWN` is not a lesser-severity middle ground between `SAFE` and
    `UNSAFE` -- it is treated identically to `UNSAFE` by
    `permits_observing()`.
    """

    SAFE = "SAFE"
    MARGINAL = "MARGINAL"
    UNSAFE = "UNSAFE"
    UNKNOWN = "UNKNOWN"


class SafetyRule(BaseModel):
    """One environmental threshold the safety monitor evaluates.

    `comparison` names how `unsafe_threshold` is applied to the named
    measurement -- e.g. ``"greater_than"``, ``"less_than"`` -- rather
    than being hardcoded per measurement, so a new sensor is
    configuration, not a new evaluation path
    (`Wayfinding_Library_Architecture.md` §2.5.6).
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    measurement: str
    comparison: str
    unsafe_threshold: float
    marginal_threshold: float | None = Field(default=None)
    staleness_bound_sec: int = Field(default=120, gt=0)


class SafetyRuleSet(BaseModel):
    """The full configured set of environmental safety rules."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    rules: list[SafetyRule] = Field(default_factory=list)
    settling_period_sec: int = Field(
        default=900,
        gt=0,
        description="Duration conditions must remain continuously safe before the enclosure may reopen.",
    )


class SafetyAssessment(BaseModel):
    """The current verdict, which rule produced it, and its freshness."""

    model_config = ConfigDict(populate_by_name=True)

    verdict: SafetyVerdict
    triggering_rule_id: str | None = Field(default=None)
    assessed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    oldest_reading_at: datetime | None = Field(default=None)

    def permits_observing(self) -> bool:
        """Return whether this assessment permits observing to continue.

        Only `SAFE` permits observing. `MARGINAL` does not by itself --
        it is a advisory severity level between `SAFE` and `UNSAFE`
        surfaced for operator awareness, not a distinct permission
        state. `UNKNOWN` is treated exactly as `UNSAFE`
        (`Wayfinding_Library_Architecture.md` §2.5.4, "Unknown Is
        Unsafe").

        Returns
        -------
        permits_observing : `bool`
            Whether this assessment permits observing to continue.
        """
        return self.verdict == SafetyVerdict.SAFE
