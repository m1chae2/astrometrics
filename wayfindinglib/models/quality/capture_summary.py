"""Purpose: The summary of what was taken with one capture setting.

Description: The unit of the target dataset report is the capture setting:
one camera and telescope with one filter, exposure length, gain setting,
offset and binning. `CaptureSettingSummary` holds the counts and times for
one such setting and its loss account. It is computed when read and never
stored.
"""

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib import CaptureSetting
from wayfindinglib.models.quality.efficiency import LossAccount


class CaptureSettingSummary(BaseModel):
    """What was taken with one capture setting.

    Parameters
    ----------
    capture_setting : `CaptureSetting`
        The setting.
    frames_taken : `int`
        How many frames were taken.
    nights : `int`
        On how many nights.
    frames_set_aside : `int`
        How many frames were set aside (for example, clouded or trailed).
    captured_seconds : `float`
        Exposure time captured, in seconds.
    weight_effective_seconds : `float` or `None`
        The weighted effective time the stacker reports, in seconds.
        ``None`` when no stack has been made.
    efficiency_effective_seconds : `float` or `None`
        Efficiency times the captured time, in seconds. ``None`` when
        efficiency cannot be found.
    loss_account : `LossAccount` or `None`
        Where the time went. ``None`` when it cannot be built.
    """

    model_config = ConfigDict(populate_by_name=True)

    capture_setting: CaptureSetting = Field(alias="captureSetting")
    frames_taken: int = Field(ge=0, alias="framesTaken")
    nights: int = Field(ge=0)
    frames_set_aside: int = Field(ge=0, alias="framesSetAside")
    captured_seconds: float = Field(ge=0.0, alias="capturedSeconds")
    weight_effective_seconds: float | None = Field(default=None, ge=0.0, alias="weightEffectiveSeconds")
    efficiency_effective_seconds: float | None = Field(
        default=None, ge=0.0, alias="efficiencyEffectiveSeconds"
    )
    loss_account: LossAccount | None = Field(default=None, alias="lossAccount")
