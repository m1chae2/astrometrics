"""Which camera profile a pipeline run used, recorded on its summary.

A camera profile (see `astrometricslib.models.camera_profile`) holds facts
about one camera model. Every pipeline's quality summary carries an
`AppliedCameraProfile`, a short copy of the facts the run relied on, so a
reader can see which assumptions a result rests on and in particular whether
the camera was recognized at all. It sits beside the profile because it
describes one.
"""

from pydantic import BaseModel, ConfigDict, Field


class AppliedCameraProfile(BaseModel):
    """Which camera profile a pipeline run used, and the numbers from it.

    A camera profile holds facts about one camera model (see
    `astrometricslib.models.camera_profile`). Recording it on each summary
    lets a reader see which assumptions a result rests on, in particular
    whether the camera was recognised at all.
    """

    model_config = ConfigDict(populate_by_name=True)

    # The camera name as the frames spell it, or `None` when the run could not
    # tell which camera took its frames.
    camera_name: str | None = Field(default=None, alias="cameraName")
    # The profile that was used. For a camera with no profile of its own this
    # is the generic stand-in.
    profile_name: str = Field(alias="profileName")
    # `True` when the camera has no profile and the generic stand-in was used.
    # Every number below is then an assumption about a made-up camera.
    is_generic_fallback: bool = Field(alias="isGenericFallback")
    # The pixel value at which this camera's frames clip, and where that
    # number came from: "datasheet", "measured" or "assumed".
    clip_ceiling_adu: float = Field(alias="clipCeilingAdu")
    clip_ceiling_source: str = Field(alias="clipCeilingSource")
    # A pixel at or above this value counts as saturated in a raw frame.
    saturation_threshold_adu: float = Field(alias="saturationThresholdAdu")
    saturation_threshold_source: str = Field(alias="saturationThresholdSource")
    # `False` when the threshold is above the ceiling, so a saturated raw frame
    # could never be counted as saturated.
    saturation_threshold_can_be_reached: bool = Field(alias="saturationThresholdCanBeReached")
    # Whether the profile has a sensitivity curve, so spectra can be corrected
    # for the sensor's sensitivity.
    has_quantum_efficiency_curve: bool = Field(alias="hasQuantumEfficiencyCurve")
