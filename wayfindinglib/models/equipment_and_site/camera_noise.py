"""Purpose: The gain and read noise of a camera, setting by setting.

Description: A camera's conversion gain (electrons per ADU, where ADU is
the analog-to-digital unit, one step of the stored pixel value) and its
read noise change with the camera's gain setting, offset and binning. The
camera profile in the configuration keeps one entry per setting, each with
a note on where the number came from. These models carry those entries to
the user interface with a status word for each number, so the screen can
say how far to trust it. The words match the validation register:
Measured, Partly measured and Design estimate.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class NoiseEvidenceStatus(StrEnum):
    """How well a gain or read noise is backed, in the register's words."""

    MEASURED = "measured"
    PARTLY_MEASURED = "partly_measured"
    DESIGN_ESTIMATE = "design_estimate"


class CameraNoiseValue(BaseModel):
    """One gain or read noise number with where it came from."""

    model_config = ConfigDict(populate_by_name=True)

    value: float = Field(
        ..., description="The number: electrons per ADU for a gain, electrons for a read noise."
    )
    status: NoiseEvidenceStatus = Field(
        ...,
        description=(
            "Measured when someone measured it on frames. Design estimate when it comes from a "
            "datasheet or an assumption. A single number is never Partly measured."
        ),
    )
    kind: str = Field(
        ..., description='How the profile records its origin: "measured", "datasheet" or "assumed".'
    )
    source: str = Field(
        ..., description="Enough detail to find the origin again: a file, a paper or the frames."
    )


class CameraNoiseSetting(BaseModel):
    """The gain and read noise of a camera at one setting."""

    model_config = ConfigDict(populate_by_name=True)

    camera_name: str = Field(..., alias="cameraName", description="The camera's name in the configuration.")
    gain_setting: str = Field(
        ...,
        alias="gainSetting",
        description="The camera's gain setting (an index, not electrons per ADU) or a DSLR's ISO.",
    )
    offset: str = Field(..., description="The camera offset.")
    binning: str = Field(..., description='The binning, such as "1x1".')
    temperature_c: float | None = Field(
        default=None, alias="temperatureC", description="Sensor temperature of the measurement, in degrees C."
    )
    gain: CameraNoiseValue | None = Field(
        default=None,
        description="The conversion gain in electrons per ADU, or None when only read noise is recorded.",
    )
    read_noise: CameraNoiseValue | None = Field(
        default=None,
        alias="readNoise",
        description="The read noise in electrons (RMS), or None when only the gain is recorded.",
    )
    status: NoiseEvidenceStatus = Field(
        ...,
        description=(
            "Measured when every number recorded here is measured. Partly measured when some are. "
            "Design estimate when none is."
        ),
    )
