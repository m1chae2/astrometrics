"""Purpose: Commissioning Evidence Domain Models.

Description: The outcome of one commissioning drill: which delegation
phase it qualifies the system to enter, what it observed against each
of that gate's criteria, and whether the operator aborted it. It exists
because several gate criteria are established outside any observing
night -- a device summary survey, an envelope-rejection check, an
enclosure cycling drill -- producing evidence with no `ObservationSession`
to attach to (`Wayfinding_Library_Architecture.md` Section 7.2 and the
"Commissioning run" entry of the site glossary, `documentation/Glossary.md`).

Append-only rather than configuration: unlike every other model in this
subsystem, a `CommissioningRun` is written once at drill completion and
never revised. A drill re-run produces a new record, so a superseded
result remains visible rather than being overwritten. Carries no
reference to
`ObservationSession` in either direction -- a commissioning run may
occur with no session, and a session may contain evidence produced by
no commissioning run.

`criterion` on each `CommissioningObservation` names the gate criterion
the drill checked. The criteria are those of Table 12 in
`Wayfinding_Library_Implementation.md` (Section 7.2), which is an
operator procedure: no module in this library applies a gate or matches
on this string, so the operator reads these records against the table.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class CommissioningObservation(BaseModel):
    """One observed value against one gate criterion."""

    model_config = ConfigDict(populate_by_name=True)

    criterion: str
    observed_value: str
    expected: str
    passed: bool
    detail: str = Field(default="")


class CommissioningRun(BaseModel):
    """The append-only outcome of one commissioning drill."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    phase: int = Field(..., ge=0, le=5, description="The delegation phase this drill qualifies.")
    drill_name: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)
    operator_note: str = Field(default="")
    observations: list[CommissioningObservation] = Field(default_factory=list)
    aborted: bool = Field(default=False)
    abort_reason: str | None = Field(default=None)

    def all_passed(self) -> bool:
        """Return whether every observation passed without the run aborting.

        Returns
        -------
        passed : `bool`
            Whether every observation passed and the run was not aborted.
        """
        if self.aborted:
            return False
        return all(o.passed for o in self.observations)
