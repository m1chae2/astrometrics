"""Purpose: Target Quality Advisory Domain Models.

Description: What the science-side archive already knows about a
target, surfaced to inform, never dictate, package authoring and
scheduling priority (`Wayfinding_Library_Architecture.md` §2.3.2).
Computed on demand from astrometricslib's public high-level interface, never
recorded. `variable_star_candidate_count` is currently always zero --
the science library's own stellar-object listing does not yet filter by
target identifier, so the cross-reference is deferred rather than
implemented against a workaround
(`Wayfinding_Library_Architecture.md` §2.3.2, §4).
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class QualityFlagSummary(BaseModel):
    """One pipeline's flagged/degraded status for a target."""

    model_config = ConfigDict(populate_by_name=True)

    pipeline_name: str
    flagged: bool = Field(default=False)
    flag_reasons: list[str] = Field(default_factory=list)


class ScienceOutcomeSummary(BaseModel):
    """Notable science outcomes already produced from existing data."""

    model_config = ConfigDict(populate_by_name=True)

    variable_star_candidate_count: int = Field(default=0, ge=0)
    asteroid_candidate_count: int = Field(default=0, ge=0)
    confirmed_asteroid_candidate_count: int = Field(default=0, ge=0)


class TargetQualityAdvisory(BaseModel):
    """The computed-on-demand quality advisory for one target."""

    model_config = ConfigDict(populate_by_name=True)

    target_id: str
    quality_flags: list[QualityFlagSummary] = Field(default_factory=list)
    science_outcomes: ScienceOutcomeSummary = Field(default_factory=ScienceOutcomeSummary)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def has_any_flagged(self) -> bool:
        """Return whether any pipeline flagged this target.

        Returns
        -------
        flagged : `bool`
            Whether any pipeline flagged this target.
        """
        return any(f.flagged for f in self.quality_flags)
