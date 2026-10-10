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
    # How many pixels the camera merged along each axis, written "<x>x<y>"
    # (for example "1x1" for none, "2x2" for four pixels read as one). Frames
    # binned differently are never mixed. `None` for a row that does not
    # record it.
    binning: str | None = Field(default=None, alias="binning")
    # The sensor temperature slot of a dark, in degrees Celsius (the
    # temperature rounded to the library's tolerance). Only darks are
    # filed by temperature, so this is `None` for biases and flats and for
    # darks whose header records no temperature.
    temperature_c: float | None = Field(default=None, alias="temperatureC")
    exposure: float | None = Field(default=None, alias="exposure")
    filter: str | None = Field(default=None, alias="filter")
    count: int = Field(..., alias="count")


class CalibrationStats(BaseModel):
    """Registered dark, flat, and bias frame statistics for the library."""

    model_config = ConfigDict(populate_by_name=True)
    darks: list[CalibrationEntry] = Field(default_factory=list, alias="darks")
    biases: list[CalibrationEntry] = Field(default_factory=list, alias="biases")
    flats: list[CalibrationEntry] = Field(default_factory=list, alias="flats")
