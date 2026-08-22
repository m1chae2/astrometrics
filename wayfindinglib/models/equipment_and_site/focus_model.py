"""Purpose: Focus Model Domain Model.

Description: The measured relationship between focuser position,
temperature, and filter -- the per-filter offsets and thermal
coefficient autofocus starts from. Foundation state for the same
reason guider calibration is: a measured, slowly changing physical
relationship rather than a per-night observation
(`Wayfinding_Library_Architecture.md` §2.2.2).
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApproachDirection(StrEnum):
    """The single direction every focus-curve sample is reached from.

    Per `Wayfinding_Library_Architecture.md` §2.5.5's "Single Approach
    Direction" invariant: a curve sampled with mixed approach directions
    has backlash folded into its shape and produces a minimum that is an
    artifact of traversal order.
    """

    INWARD = "inward"
    OUTWARD = "outward"


class FilterFocusOffset(BaseModel):
    """The focuser-step offset for one filter, relative to the baseline."""

    model_config = ConfigDict(populate_by_name=True)

    filter: str
    offset_steps: int


class FocusModel(BaseModel):
    """Measured backlash, approach direction, and thermal behavior."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    camera_id: str
    telescope_id: str
    backlash_steps: int = Field(..., ge=0)
    approach_direction: ApproachDirection
    thermal_coefficient_steps_per_c: float = Field(default=0.0)
    filter_offsets: list[FilterFocusOffset] = Field(default_factory=list)
    calibrated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _check_filter_offsets_unique(self) -> FocusModel:
        """Verify each filter has at most one offset entry.

        Returns
        -------
        focus_model : `FocusModel`
            This focus model, unchanged, once validated.

        Raises
        ------
        ValueError
            Raised if `filter_offsets` contains more than one entry for
            the same filter.
        """
        filters = [o.filter for o in self.filter_offsets]
        if len(filters) != len(set(filters)):
            raise ValueError("filter_offsets contains duplicate filter entries")
        return self

    def offset_for_filter(self, filter_name: str) -> int:
        """Return the offset for `filter_name`, or 0 if none is configured.

        Returns
        -------
        offset_steps : `int`
            The configured offset, or 0 if none is configured.
        """
        return next((o.offset_steps for o in self.filter_offsets if o.filter == filter_name), 0)
