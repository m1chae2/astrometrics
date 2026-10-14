"""Purpose: The record of one capture run with the main camera.

Description: `control.imaging.capture_image` takes one or more exposures,
optionally after turning the filter wheel and with dithering between
frames. `CaptureResult` says what it did.
"""

from pydantic import BaseModel, ConfigDict, Field


class CaptureResult(BaseModel):
    """What one `control.imaging.capture_image` call took.

    Attributes
    ----------
    frames_captured : `int`
        How many exposures the camera started and finished.
    exposure_seconds : `float`
        The length of each exposure.
    filter_name : `str` or `None`
        The filter the wheel was turned to first, or `None` if it was
        left where it was.
    dithers : `int`
        How many times the pointing was shifted between frames.
    """

    model_config = ConfigDict(populate_by_name=True)

    frames_captured: int = Field(default=0, ge=0, alias="framesCaptured")
    exposure_seconds: float = Field(..., gt=0.0, alias="exposureSeconds")
    filter_name: str | None = Field(default=None, alias="filterName")
    dithers: int = Field(default=0, ge=0, alias="dithers")
