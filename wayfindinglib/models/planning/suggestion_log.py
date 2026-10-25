"""Purpose: The suggestion log and the result of a suggestion refresh.

Description: The library suggests observation packages and keeps a log of
what happened to each suggestion: created, updated, deleted (with the cause),
dismissed, completed and so on. Each log line names the evidence behind it
(the stored measurement row ids and the values). The log lets a person check
later whether a suggestion was a good one (issue #96, step 2).

`PackageChange` is what `ObservationPlanning.refresh_suggestions` returns. It
lists the ids of the packages the refresh added, updated, deleted, completed
or reverted, so the backend can send one "packages changed" event.
"""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class SuggestionEvent(StrEnum):
    """What happened to a suggestion or to a package the refresh looked at."""

    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    DISMISSED = "dismissed"
    UNDISMISSED = "undismissed"
    COMPLETED = "completed"
    REOPENED = "reopened"
    PROMOTED = "promoted"


class SuggestionLogEntry(BaseModel):
    """One line of the suggestion lifecycle log.

    Parameters
    ----------
    id : `str`
        A unique id for the line.
    time : `datetime`
        When it happened (UTC).
    event : `SuggestionEvent`
        What happened.
    package_id : `str`
        The package it happened to.
    target_id : `str`
        The package's target.
    rule_name : `str`
        The rule behind the suggestion, or ``"ranking"``.
    source_key : `str`
        What the suggestion came from. Empty for the whole package.
    cause : `str`
        Why it happened, such as ``"goal met"`` or ``"night ended"``.
    evidence_row_ids : `list` [`int`]
        The stored measurement rows behind the suggestion.
    values : `dict` [`str`, `float` or `str` or `None`]
        The numbers that mattered, such as the noise, the goal and the
        proposed frame count.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event: SuggestionEvent
    package_id: str
    target_id: str = ""
    rule_name: str = ""
    source_key: str = ""
    cause: str = ""
    evidence_row_ids: list[int] = Field(default_factory=list)
    values: dict[str, float | str | None] = Field(default_factory=dict)


class PackageChange(BaseModel):
    """What one suggestion refresh changed, as lists of package ids.

    Parameters
    ----------
    added : `list` [`str`]
        Suggested packages created.
    updated : `list` [`str`]
        Suggested packages whose rows or reason changed.
    deleted : `list` [`str`]
        Suggested packages removed (goal met, the rule stopped asking, or
        the night ended).
    completed : `list` [`str`]
        Planned packages found complete.
    reverted : `list` [`str`]
        Complete packages set back to planned because frames were set aside.
    checklist_item_ids : `list` [`str`]
        Checklist items added to tonight's session.
    notes : `list` [`str`]
        Plain sentences about anything skipped, such as a target whose report
        could not be built. Missing data never deletes a suggestion.
    as_of : `datetime`
        When the refresh ran (UTC).
    """

    model_config = ConfigDict(populate_by_name=True)

    added: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)
    completed: list[str] = Field(default_factory=list)
    reverted: list[str] = Field(default_factory=list)
    checklist_item_ids: list[str] = Field(default_factory=list, alias="checklistItemIds")
    notes: list[str] = Field(default_factory=list)
    as_of: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="asOf")

    @property
    def is_empty(self) -> bool:
        """Whether the refresh changed nothing.

        Returns
        -------
        empty : `bool`
            `True` when no id is listed.
        """
        return not (
            self.added
            or self.updated
            or self.deleted
            or self.completed
            or self.reverted
            or self.checklist_item_ids
        )
