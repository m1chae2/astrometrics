"""Purpose: The imaging sequence plan the app's sequencer queue runs.

Description: `ObservationPlanning.create_plan(kind="sequence")` turns a
list of ``{count, exposure, filter}`` items for one target into a
`SequencePlan`, adding each item's duration and the plan's total. The
plan is not stored; the backend's sequencer keeps it in its own queue.
"""

from pydantic import BaseModel, ConfigDict, Field


class SequenceItem(BaseModel):
    """A single instruction block inside an imaging sequence plan."""

    model_config = ConfigDict(populate_by_name=True)
    count: int = Field(..., alias="count")
    exposure: float = Field(..., alias="exposure")
    filter: str = Field(..., alias="filter")
    duration: float = Field(..., alias="duration")


class SequencePlan(BaseModel):
    """A full structured session queue of planned exposure sequences."""

    model_config = ConfigDict(populate_by_name=True)
    id: str = Field(..., alias="id")
    target_name: str = Field(..., alias="target_name")
    items: list[SequenceItem] = Field(default_factory=list, alias="items")
    total_duration: float = Field(default=0.0, alias="total_duration")
    created_at: str = Field(..., alias="created_at")
    status: str = Field(default="planned", alias="status")
