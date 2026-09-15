"""Purpose: Divergence Record Domain Model.

Description: One measured comparison between an action this system
computed and the action the delegated system took, for one capability
at one moment -- the evidence a promotion gate is decided on
(`Wayfinding_Library_Architecture.md` §2.4.4). Records are written
whether or not the comparison agreed, since a promotion gate is an
agreement *rate*, which cannot be computed from disagreements alone
(the "Evidence Is Symmetric" invariant). Pairing is by
`comparison_input_id` -- the identifier of the shared measurement both
systems responded to -- rather than by timestamp proximity, which would
silently pair a computed correction against an unrelated one under load
(`Wayfinding_Library_Architecture.md` §2.4.2).
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from wayfindinglib.models.policy.delegation import ObservatoryCapability


class DivergenceRecord(BaseModel):
    """One comparison between a computed action and the delegated system."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="id")
    observation_session_id: str = Field(alias="observationSessionId")
    queued_observation_package_id: str | None = Field(default=None, alias="queuedObservationPackageId")
    capability: ObservatoryCapability = Field(alias="capability")
    comparison_input_id: str = Field(alias="comparisonInputId")
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="observedAt")
    intended_value: float = Field(alias="intendedValue")
    observed_value: float = Field(alias="observedValue")
    divergence_magnitude: float = Field(
        alias="divergenceMagnitude",
        description="Signed: intended minus observed, so systematic bias is "
        "distinguishable from symmetric noise.",
    )
    divergence_unit: str = Field(alias="divergenceUnit")
    tolerance: float = Field(
        alias="tolerance",
        description="The tolerance this comparison was evaluated against, so a later aggregate "
        "report can recompute e.g. 'within half tolerance' without re-deriving a value this "
        "record already used.",
    )
    within_tolerance: bool = Field(alias="withinTolerance")
    converged: bool | None = Field(
        default=None,
        alias="converged",
        description="For PLATE_SOLVE_ALIGNMENT comparisons only: whether the underlying "
        "PointingCorrection converged within its iteration limit. None for every other "
        "capability, and for alignment comparisons recorded before this field existed.",
    )
    detail: str = Field(default="", alias="detail")
