"""Purpose: Calibration Inventory Domain Models.

Description: Counts of previously captured calibration frames, grouped
by camera, exposure, and filter. Foundation-level reference data: they
describe what exists and are consulted by Observation Planning, never
written by a running session (`Wayfinding_Library_Architecture.md`
§2.2.2). `CalibrationAdvisory` is the computed-on-demand view surfaced
during package authoring -- informational only, never adjusting
placement or priority.

`CalibrationEntry`/`CalibrationStats` supersede the deprecated
`observation.CalibrationEntry`/`CalibrationStats` (which had no writer
in the codebase); `post_session_reconciliation` closes that gap
(`Wayfinding_Library_Architecture.md` §2.2.2).
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib import FilterType
from wayfindinglib.models.planning.observation_package import FrameType


class CalibrationEntry(BaseModel):
    """Counted calibration frames of one type/exposure/filter, per camera."""

    model_config = ConfigDict(populate_by_name=True)

    camera_id: str
    frame_type: FrameType
    exposure_sec: float | None = Field(default=None)
    filter: FilterType | None = Field(default=None)
    count: int = Field(default=0, ge=0)


class CalibrationStats(BaseModel):
    """The full calibration inventory for one camera."""

    model_config = ConfigDict(populate_by_name=True)

    camera_id: str
    darks: list[CalibrationEntry] = Field(default_factory=list)
    biases: list[CalibrationEntry] = Field(default_factory=list)
    flats: list[CalibrationEntry] = Field(default_factory=list)


class CalibrationAdvisory(BaseModel):
    """Computed-on-demand inventory lookup for one requested calibration entry.

    Never persisted; purely informational
    (`Wayfinding_Library_Architecture.md` §2.3.4, "Advisory, Not
    Authority").
    """

    model_config = ConfigDict(populate_by_name=True)

    camera_id: str
    frame_type: FrameType
    exposure_sec: float | None = Field(default=None)
    filter: FilterType | None = Field(default=None)
    existing_count: int = Field(default=0, ge=0)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
