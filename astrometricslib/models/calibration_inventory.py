"""Purpose: Calibration library inventory models.

Description: `CalibrationEntry` and `CalibrationStats` describe how many
dark, bias and flat frames the calibration library holds for each camera
setting. They give the shape of what `CalibrationLibrary.get_stats`
returns. The backend returns that data and the UI's types are generated
from these models.
"""

import logging

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class CalibrationEntry(BaseModel):
    """Metadata for a single registered calibration frame."""

    model_config = ConfigDict(populate_by_name=True)
    camera: str = Field(..., alias="camera")
    iso: str = Field(..., alias="iso")
    # The camera's offset setting (a constant added to every pixel). Frames
    # taken at different offsets are never mixed, so each offset is its own
    # row. `None` when the frame's header does not record it.
    offset: float | None = Field(default=None, alias="offset")
    exposure: float | None = Field(default=None, alias="exposure")
    filter: str | None = Field(default=None, alias="filter")
    count: int = Field(..., alias="count")


class CalibrationStats(BaseModel):
    """Registered dark, flat, and bias frame statistics for the library."""

    model_config = ConfigDict(populate_by_name=True)
    darks: list[CalibrationEntry] = Field(default_factory=list, alias="darks")
    biases: list[CalibrationEntry] = Field(default_factory=list, alias="biases")
    flats: list[CalibrationEntry] = Field(default_factory=list, alias="flats")
