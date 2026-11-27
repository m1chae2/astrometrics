"""Purpose: Calibration library inventory models.

Description: `CalibrationEntry` and `CalibrationStats` describe how many
dark, bias and flat frames the calibration library holds for each camera
setting. They give the shape of what `CalibrationLibrary.get_stats`
returns. The backend returns that data and the UI's types are generated
from these models.

`CalibrationSetCount` is one row of `calibration.query`: how many frames
the library holds of one kind at one setting. `CalibrationMatch` is one
group of `calibration.match`: light frames of one setting, with the darks,
biases and flats the stacker would use for them.
"""

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib.models.stellar_source import CalibrationGroupMatch

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


class CalibrationSetCount(BaseModel):
    """How many frames the library holds of one kind at one setting.

    The settings are written as the library files them: the gain as text
    (a gain setting or an ISO), the binning as ``"<x>x<y>"``.

    Attributes
    ----------
    kind : {"dark", "bias", "flat"}
        The kind of frame.
    camera : `str`
        The camera name the frames are filed under.
    gain : `str` or `None`
        The gain setting (or ISO), as text.
    offset : `float` or `None`
        The camera offset.
    binning : `str` or `None`
        The binning, such as ``"1x1"``.
    temperature_c : `float` or `None`
        A dark's temperature slot, in degrees Celsius. `None` for biases and
        flats.
    exposure_s : `float` or `None`
        A dark's exposure, in seconds. `None` for biases and flats.
    filter_name : `str` or `None`
        A flat's filter. `None` for darks and biases.
    count : `int`
        How many frames the library holds at this setting.
    row_key : `str`
        A key no other row shares. `CalibrationSetHealth.row_key` gives the
        same set the same key, so a display can join the two.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["dark", "bias", "flat"]
    camera: str
    gain: str | None = None
    offset: float | None = None
    binning: str | None = None
    temperature_c: float | None = None
    exposure_s: float | None = None
    filter_name: str | None = None
    count: int = Field(ge=0)
    row_key: str


class CalibrationMatch(BaseModel):
    """One group of light frames and the calibration the stacker would use.

    The frames of a group share a camera, optic, filter, gain, offset and
    exposure. The dark, bias and flat matches come from the selection the
    stacking stage itself runs, so a match here is a match when stacking.

    Attributes
    ----------
    camera : `str`
        The camera name of the frames.
    focal_length_mm : `int` or `None`
        The optic's focal length in whole millimeters, or `None` when the
        frames do not record it.
    filter_name : `str`
        The filter, such as ``"L"``; ``"None"`` when the frames have none.
    gain : `str`
        The gain setting (or ISO) the frames record, as text.
    offset : `str`
        The camera offset the frames record, as text.
    binning : `str` or `None`
        The binning the stacker matches the calibration with.
    exposure_s : `float`
        The exposure of one frame, in seconds.
    frame_count : `int`
        How many light frames are in the group.
    dark, bias, flat : `CalibrationGroupMatch`
        What the stacker would use of each kind: the frame count, the
        settings it fell back to and why nothing matched.
    """

    model_config = ConfigDict(frozen=True)

    camera: str
    focal_length_mm: int | None = None
    filter_name: str
    gain: str
    offset: str
    binning: str | None = None
    exposure_s: float
    frame_count: int = Field(ge=0)
    dark: CalibrationGroupMatch
    bias: CalibrationGroupMatch
    flat: CalibrationGroupMatch
